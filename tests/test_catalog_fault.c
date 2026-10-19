#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "storage/catalog.h"

static int calls;
static int fail_at;
static long live;

void *catalog_test_malloc(size_t size)
{
    if (++calls == fail_at) return NULL;
    void *memory = malloc(size);
    if (memory != NULL) live++;
    return memory;
}

void *catalog_test_realloc(void *old, size_t size)
{
    if (++calls == fail_at) return NULL;
    void *memory = realloc(old, size);
    if (memory != NULL && old == NULL) live++;
    return memory;
}

void catalog_test_free(void *memory)
{
    if (memory != NULL) live--;
    free(memory);
}

static catalog_column_spec_t column(const char *name, value_type_t type)
{
    return (catalog_column_spec_t){name, strlen(name), type, false, NULL};
}

static catalog_table_id_t add(catalog_t *catalog, const char *name)
{
    catalog_column_spec_t columns[] = {column("id", VALUE_INTEGER), column("payload", VALUE_BLOB)};
    catalog_table_spec_t spec = {name, strlen(name), columns, 2, 0, true};
    catalog_table_id_t id = 0;
    assert(catalog_create_table(catalog, &spec, &id) == CATALOG_OK);
    return id;
}

static catalog_t *four_tables(void)
{
    catalog_t *catalog;
    assert(catalog_open_empty(&catalog) == CATALOG_OK);
    const char *names[] = {"a", "b", "c", "d"};
    for (size_t i = 0; i < 4; i++) assert(add(catalog, names[i]) == i + 1);
    return catalog;
}

typedef struct {
    uint8_t *bytes;
    size_t size;
} snapshot_t;

static snapshot_t snapshot(const catalog_t *catalog)
{
    snapshot_t shot;
    int saved = fail_at;
    fail_at = 0;
    assert(catalog_encode(catalog, &shot.bytes, &shot.size) == CATALOG_OK);
    fail_at = saved;
    return shot;
}

static void assert_same(const catalog_t *catalog, const snapshot_t *before)
{
    snapshot_t now = snapshot(catalog);
    assert(now.size == before->size && memcmp(now.bytes, before->bytes, now.size) == 0);
    catalog_test_free(now.bytes);
}

/*
 * #6: 어느 할당이 실패해도 NOMEM이고 카탈로그·카운터·출력 인자가 그대로이며 새어 나간 메모리가 없다.
 * 각 호출이 필요한 할당 횟수보다 큰 fail_at에서 처음 성공한다.
 */
static void create_preserves_catalog(void)
{
    catalog_t *catalog = four_tables();
    snapshot_t before = snapshot(catalog);
    uint64_t generation = catalog_generation(catalog);
    long baseline = live;
    catalog_column_spec_t columns[] = {column("id", VALUE_INTEGER), column("payload", VALUE_BLOB)};
    catalog_table_spec_t spec = {"e", 1, columns, 2, 0, true};
    int failures = 0;
    for (int step = 1; step <= 40; step++) {
        catalog_table_id_t id = 99;
        calls = 0;
        fail_at = step;
        catalog_status_t status = catalog_create_table(catalog, &spec, &id);
        fail_at = 0;
        if (status == CATALOG_OK) {
            assert(calls < step && id == 5);
            break;
        }
        assert(status == CATALOG_NOMEM && id == 99);
        assert(live == baseline && catalog_generation(catalog) == generation);
        assert_same(catalog, &before);
        failures++;
    }
    assert(failures >= 4);
    catalog_table_view_t view;
    assert(catalog_describe(catalog, 5, &view) == CATALOG_OK);
    assert(view.row_count == 0u);
    catalog_test_free(before.bytes);
    catalog_close(catalog);
    assert(live == 0);
}

static void open_empty_failure_keeps_output(void)
{
    static char marker;
    for (int step = 1; step <= 4; step++) {
        catalog_t *out = (catalog_t *)&marker;
        calls = 0;
        fail_at = step;
        catalog_status_t status = catalog_open_empty(&out);
        fail_at = 0;
        if (status == CATALOG_OK) {
            catalog_close(out);
            break;
        }
        assert(status == CATALOG_NOMEM && out == (catalog_t *)&marker && live == 0);
    }
    assert(live == 0);
}

static void encode_failure_keeps_output(void)
{
    catalog_t *catalog = four_tables();
    uint8_t *bytes = (uint8_t *)&calls;
    size_t size = 123;
    calls = 0;
    fail_at = 1;
    assert(catalog_encode(catalog, &bytes, &size) == CATALOG_NOMEM);
    fail_at = 0;
    assert(bytes == (uint8_t *)&calls && size == 123);
    catalog_close(catalog);
    assert(live == 0);
}

static void decode_failure_keeps_output_and_frees_partial_work(void)
{
    catalog_t *catalog = four_tables();
    snapshot_t shot = snapshot(catalog);
    long baseline = live;
    static char marker;
    int failures = 0;
    for (int step = 1; step <= 200; step++) {
        catalog_t *out = (catalog_t *)&marker;
        calls = 0;
        fail_at = step;
        catalog_status_t status = catalog_decode(shot.bytes, shot.size, &out);
        fail_at = 0;
        if (status == CATALOG_OK) {
            assert(calls < step);
            assert_same(out, &shot);
            catalog_close(out);
            break;
        }
        assert(status == CATALOG_NOMEM && out == (catalog_t *)&marker);
        assert(live == baseline);
        failures++;
    }
    assert(failures >= 8);
    catalog_test_free(shot.bytes);
    catalog_close(catalog);
    assert(live == 0);
}

static catalog_t *clone_fault_fixture(void)
{
    catalog_t *catalog = four_tables();
    assert(catalog_drop_table(catalog, 2) == CATALOG_OK);
    value_view_t defaults[] = {
        {.type = VALUE_INTEGER, .integer = -17}, {.type = VALUE_REAL, .real = 2.75},
        {.type = VALUE_TEXT, .bytes = (const uint8_t *)"a\0z", .size_bytes = 3},
        {.type = VALUE_BLOB, .bytes = (const uint8_t *)"\x00\xff", .size_bytes = 2},
        {.type = VALUE_TEXT}, {.type = VALUE_BLOB},
    };
    catalog_column_spec_t columns[] = {
        {"i", 1, VALUE_INTEGER, true, &defaults[0]}, {"r", 1, VALUE_REAL, false, &defaults[1]},
        {"t", 1, VALUE_TEXT, false, &defaults[2]}, {"b", 1, VALUE_BLOB, false, &defaults[3]},
        {"et", 2, VALUE_TEXT, false, &defaults[4]}, {"eb", 2, VALUE_BLOB, false, &defaults[5]},
    };
    catalog_table_spec_t spec = {"defaults", 8, columns, 6, 0, true};
    catalog_table_id_t id;
    assert(catalog_create_table(catalog, &spec, &id) == CATALOG_OK && id == 5);
    assert(catalog_set_row_count(catalog, id, 987) == CATALOG_OK);
    assert(catalog_set_locators(catalog, id, 71, 72) == CATALOG_OK);
    assert(catalog_raise_identity(catalog, id, 1234) == CATALOG_OK);
    assert(catalog_add_index(catalog, id, "unique_real", 11, 1, true, false) == CATALOG_OK);
    assert(catalog_add_index(catalog, id, "ordered_text", 12, 2, false, true) == CATALOG_OK);
    assert(catalog_set_index_root(catalog, "unique_real", 11, 81) == CATALOG_OK);
    assert(catalog_set_index_root(catalog, "ordered_text", 12, 82) == CATALOG_OK);
    return catalog;
}

static void clone_failures_preserve_source_output_and_allocations(catalog_t *source)
{
    snapshot_t before = snapshot(source);
    uint64_t generation = catalog_generation(source);
    long baseline = live;
    static char marker;
    bool succeeded = false;
    int failures = 0;
    for (int step = 1; step <= 200; step++) {
        catalog_t *out = (catalog_t *)&marker;
        calls = 0;
        fail_at = step;
        catalog_status_t status = catalog_clone(source, &out);
        int clone_calls = calls;
        fail_at = 0;
        assert(catalog_generation(source) == generation);
        assert_same(source, &before);
        if (status == CATALOG_OK) {
            assert(clone_calls < step && failures == step - 1 && out != source);
            assert_same(out, &before);
            catalog_close(out);
            assert(live == baseline);
            succeeded = true;
            break;
        }
        assert(status == CATALOG_NOMEM && out == (catalog_t *)&marker);
        assert(live == baseline);
        failures++;
    }
    assert(succeeded && failures > 0);
    printf("catalog clone: %zu tables, %d allocation failures preserve source/output/live allocations\n",
           catalog_table_count(source), failures);
    catalog_test_free(before.bytes);
    catalog_close(source);
    assert(live == 0);
}

int main(void)
{
    clone_failures_preserve_source_output_and_allocations(clone_fault_fixture());
    catalog_t *empty;
    assert(catalog_open_empty(&empty) == CATALOG_OK);
    clone_failures_preserve_source_output_and_allocations(empty);
    create_preserves_catalog();
    open_empty_failure_keeps_output();
    encode_failure_keeps_output();
    decode_failure_keeps_output_and_frees_partial_work();
    puts("catalog: allocation failures keep catalog, counters and outputs; no leaks");
    return 0;
}
