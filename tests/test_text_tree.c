/* #65: binary TEXT 색인의 분할·중복·삭제·재게시 계약. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "storage/text_tree.h"

#define TEST_PAGES 4096u
#define TABLE_ID 7u

typedef struct {
    uint8_t (*pages)[PAGE_BODY_BYTES];
    bool live[TEST_PAGES];
    bool fresh[TEST_PAGES];
    uint32_t next;
    size_t reads;
} store_t;

static pk_status_t read_page(void *context, uint32_t page, uint8_t body[PAGE_BODY_BYTES])
{
    store_t *store = context;
    store->reads++;
    if (page >= store->next || !store->live[page]) return PK_CORRUPT;
    memcpy(body, store->pages[page], PAGE_BODY_BYTES);
    return PK_OK;
}

static pk_status_t write_page(void *context, uint32_t page, const uint8_t body[PAGE_BODY_BYTES])
{
    store_t *store = context;
    if (page >= store->next || !store->fresh[page]) return PK_CORRUPT;
    memcpy(store->pages[page], body, PAGE_BODY_BYTES);
    return PK_OK;
}

static pk_status_t allocate_page(void *context, uint32_t *page)
{
    store_t *store = context;
    if (store->next == TEST_PAGES) return PK_NOMEM;
    *page = store->next++;
    store->live[*page] = true;
    store->fresh[*page] = true;
    return PK_OK;
}

static pk_status_t release_page(void *context, uint32_t page)
{
    store_t *store = context;
    if (page >= store->next || !store->live[page]) return PK_CORRUPT;
    store->live[page] = false;
    return PK_OK;
}

static bool is_fresh(void *context, uint32_t page)
{
    return ((store_t *)context)->fresh[page];
}

static void commit(store_t *store)
{
    memset(store->fresh, 0, sizeof(store->fresh));
}

static bool count_page(void *context, uint32_t page)
{
    (void)page;
    ++*(size_t *)context;
    return true;
}

static void key_for(unsigned n, char out[8])
{
    int size = snprintf(out, 8, "k%05u", n);
    assert(size == 6);
}

typedef struct {
    unsigned first;
    unsigned count;
    unsigned stop_after;
} range_result_t;

static bool collect_range(void *context, const uint8_t *key, size_t key_bytes, heap_locator_t locator)
{
    range_result_t *result = context;
    char expected[8];
    key_for(result->first + result->count, expected);
    assert(key_bytes == 6 && memcmp(key, expected, key_bytes) == 0);
    assert(locator.directory_index == result->first + result->count);
    result->count++;
    return result->stop_after == 0 || result->count < result->stop_after;
}

static bool collect_long_range(void *context, const uint8_t *key, size_t key_bytes, heap_locator_t locator)
{
    range_result_t *result = context;
    char prefix[5];
    snprintf(prefix, sizeof(prefix), "L%03u", result->first + result->count);
    assert(key_bytes == TEXT_TREE_KEY_MAX_BYTES && memcmp(key, prefix, 4) == 0);
    assert(locator.directory_index == 1000u + result->first + result->count);
    result->count++;
    return true;
}

static void check_long_ranges(const pk_tree_io_t *io, uint32_t root)
{
    uint8_t lower[TEXT_TREE_KEY_MAX_BYTES], upper[TEXT_TREE_KEY_MAX_BYTES];
    memset(lower, 'x', sizeof(lower));
    memset(upper, 'x', sizeof(upper));
    memcpy(lower, "L020", 4);
    memcpy(upper, "L080", 4);
    text_key_bound_t low = {lower, sizeof(lower), false};
    text_key_bound_t high = {upper, sizeof(upper), true};
    range_result_t result = {21, 0, 0};
    bool stopped = false;
    assert(text_tree_visit_range(io, TABLE_ID, root, (text_key_range_t){&low, &high},
                                  collect_long_range, &result, &stopped) == PK_OK);
    assert(!stopped && result.count == 60);
}

static void check_ranges(const pk_tree_io_t *io, uint32_t root, store_t *store)
{
    text_key_bound_t low = {(const uint8_t *)"k00250", 6, true};
    text_key_bound_t high = {(const uint8_t *)"k00540", 6, true};
    for (unsigned flags = 0; flags < 4; flags++) {
        low.inclusive = (flags & 1u) != 0;
        high.inclusive = (flags & 2u) != 0;
        range_result_t result = {250u + !low.inclusive, 0, 0};
        bool stopped = true;
        assert(text_tree_visit_range(io, TABLE_ID, root, (text_key_range_t){&low, &high},
                                      collect_range, &result, &stopped) == PK_OK);
        assert(!stopped && result.count == 289u + low.inclusive + high.inclusive);
    }
    size_t pages = 0;
    assert(text_tree_visit_pages(io, TABLE_ID, root, count_page, &pages) == PK_OK);
    store->reads = 0;
    low = (text_key_bound_t){(const uint8_t *)"k00590", 6, true};
    range_result_t result = {590, 0, 3};
    bool stopped = false;
    assert(text_tree_visit_range(io, TABLE_ID, root, (text_key_range_t){&low, NULL},
                                  collect_range, &result, &stopped) == PK_OK);
    assert(stopped && result.count == 3 && store->reads < pages);
    printf("text tree range: read=%zu total=%zu visited=%u stopped=%u\n", store->reads, pages, result.count, stopped);
    result = (range_result_t){0, 0, 0};
    assert(text_tree_visit_range(io, TABLE_ID, root, (text_key_range_t){NULL, NULL},
                                  collect_range, &result, &stopped) == PK_OK);
    assert(!stopped && result.count == 600);
    result = (range_result_t){0, 0, 0};
    high = (text_key_bound_t){(const uint8_t *)"k00002", 6, false};
    assert(text_tree_visit_range(io, TABLE_ID, root, (text_key_range_t){NULL, &high},
                                  collect_range, &result, &stopped) == PK_OK);
    assert(!stopped && result.count == 2);
    result = (range_result_t){0, 0, 0};
    assert(text_tree_visit_range(io, TABLE_ID, root, (text_key_range_t){&low, &high},
                                  collect_range, &result, &stopped) == PK_OK);
    assert(!stopped && result.count == 0);
    high = (text_key_bound_t){NULL, 1, true};
    assert(text_tree_visit_range(io, TABLE_ID, root, (text_key_range_t){NULL, &high},
                                  collect_range, &result, &stopped) == PK_LIMIT);
    assert(text_tree_visit_range(io, TABLE_ID, TEST_PAGES, (text_key_range_t){NULL, NULL},
                                  collect_range, &result, &stopped) == PK_CORRUPT);
    uint8_t saved = store->pages[root][0];
    store->pages[root][0] = 0;
    assert(text_tree_visit_range(io, TABLE_ID, root, (text_key_range_t){NULL, NULL},
                                  collect_range, &result, &stopped) == PK_CORRUPT);
    store->pages[root][0] = saved;
}

int main(void)
{
    store_t *store = calloc(1, sizeof(*store));
    assert(store != NULL);
    store->pages = calloc(TEST_PAGES, PAGE_BODY_BYTES);
    assert(store->pages != NULL);
    store->next = 2;
    pk_tree_io_t io = {store, read_page, write_page, allocate_page, release_page, is_fresh};
    uint32_t root = 0;
    assert(text_tree_create(&io, TABLE_ID, &root) == PK_OK);
    commit(store);

    for (unsigned i = 0; i < 600; i++) {
        char key[8];
        key_for(i, key);
        heap_locator_t at = {i, (uint16_t)i};
        pk_status_t inserted = text_tree_put(&io, TABLE_ID, &root, (const uint8_t *)key, 6, at, false, NULL);
        if (inserted != PK_OK) fprintf(stderr, "insert %u status %d\n", i, inserted);
        assert(inserted == PK_OK);
        commit(store);
    }
    for (unsigned i = 0; i < 600; i++) {
        char key[8];
        key_for(i, key);
        heap_locator_t found;
        assert(text_tree_find(&io, TABLE_ID, root, (const uint8_t *)key, 6, &found) == PK_OK);
        assert(found.directory_index == i && found.slot == i);
        assert(text_tree_put(&io, TABLE_ID, &root, (const uint8_t *)key, 6, found, false, NULL) == PK_EXISTS);
    }
    check_ranges(&io, root, store);
    for (unsigned i = 0; i < 600; i += 2) {
        char key[8];
        key_for(i, key);
        heap_locator_t removed;
        assert(text_tree_remove(&io, TABLE_ID, &root, (const uint8_t *)key, 6, &removed) == PK_OK);
        assert(removed.directory_index == i);
        commit(store);
    }
    for (unsigned i = 0; i < 600; i++) {
        char key[8];
        key_for(i, key);
        heap_locator_t found;
        pk_status_t status = text_tree_find(&io, TABLE_ID, root, (const uint8_t *)key, 6, &found);
        assert(status == (i % 2 == 0 ? PK_NOT_FOUND : PK_OK));
    }
    size_t short_key_pages = 0;
    assert(text_tree_visit_pages(&io, TABLE_ID, root, count_page, &short_key_pages) == PK_OK);
    assert(short_key_pages < 10);
    for (unsigned i = 0; i < 100; i++) {
        uint8_t key[TEXT_TREE_KEY_MAX_BYTES];
        char prefix[5];
        snprintf(prefix, sizeof(prefix), "L%03u", i);
        memset(key, 'x', sizeof(key));
        memcpy(key, prefix, 4);
        heap_locator_t at = {1000u + i, (uint16_t)i};
        assert(text_tree_put(&io, TABLE_ID, &root, key, sizeof(key), at, false, NULL) == PK_OK);
        commit(store);
    }
    for (unsigned i = 0; i < 100; i++) {
        uint8_t key[TEXT_TREE_KEY_MAX_BYTES];
        char prefix[5];
        snprintf(prefix, sizeof(prefix), "L%03u", i);
        memset(key, 'x', sizeof(key));
        memcpy(key, prefix, 4);
        heap_locator_t found;
        assert(text_tree_find(&io, TABLE_ID, root, key, sizeof(key), &found) == PK_OK);
        assert(found.directory_index == 1000u + i);
    }
    check_long_ranges(&io, root);
    assert(text_tree_restamp(&io, TABLE_ID, &root, 77) == PK_OK);
    uint32_t stamp = 0;
    assert(text_tree_stamp(&io, TABLE_ID, root, &stamp) == PK_OK && stamp == 77);
    size_t visited = 0;
    assert(text_tree_visit_pages(&io, TABLE_ID, root, count_page, &visited) == PK_OK && visited > 1);
    uint8_t too_long[TEXT_TREE_KEY_MAX_BYTES + 1] = {0};
    heap_locator_t found;
    assert(text_tree_find(&io, TABLE_ID, root, too_long, sizeof(too_long), &found) == PK_LIMIT);

    free(store->pages);
    free(store);
    puts("text tree: passed");
    return 0;
}
