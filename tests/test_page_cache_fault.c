#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../src/storage/page_cache.h"
#include "storage/page_file.h"

static size_t calls;
static size_t frees;
static size_t live_blocks;
static size_t fail_at;

void *cache_test_malloc(size_t size)
{
    calls++;
    if (calls == fail_at) return NULL;
    void *block = malloc(size);
    assert(block != NULL);
    live_blocks++;
    return block;
}

void *cache_test_calloc(size_t count, size_t size)
{
    void *block = cache_test_malloc(count * size);
    if (block != NULL) memset(block, 0, count * size);
    return block;
}

void cache_test_free(void *block)
{
    if (block == NULL) return;
    assert(live_blocks > 0);
    live_blocks--;
    frees++;
    free(block);
}

static void assert_stats_zero(page_cache_t *cache)
{
    page_cache_stats_t stats = page_cache_stats(cache);
    assert(stats.hits == 0);
    assert(stats.misses == 0);
    assert(stats.evictions == 0);
}

static void assert_body_hit(page_cache_t *cache, uint32_t id)
{
    uint8_t body[PAGE_BODY_BYTES], expected[PAGE_BODY_BYTES];
    uint16_t kind = 0;
    uint32_t table = 0;
    memset(expected, 0xb7, sizeof(expected));
    assert(page_cache_get(cache, id, &kind, &table, body));
    assert(kind == PAGE_KIND_INDEX);
    assert(table == 29);
    assert(memcmp(body, expected, sizeof(body)) == 0);
}

static void test_reset_invalidates_collisions_and_reuses_allocations(void)
{
    calls = frees = 0;
    page_cache_t *cache = page_cache_create(2);
    assert(cache != NULL);
    assert(calls == 3 && live_blocks == 3);
    uint8_t body[PAGE_BODY_BYTES];
    memset(body, 0x31, sizeof(body));
    page_cache_put(cache, 3, PAGE_KIND_RECORD, 7, body);
    assert(calls == 4 && live_blocks == 4);
    page_cache_put(cache, 7, PAGE_KIND_RECORD, 7, body);
    page_cache_drop(cache, 3);
    page_cache_put(cache, 11, PAGE_KIND_RECORD, 7, body);
    page_cache_put(cache, 15, PAGE_KIND_RECORD, 7, body);
    assert(page_cache_stats(cache).evictions == 1);

    page_cache_reset(cache);
    assert(calls == 4 && frees == 0 && live_blocks == 4);
    assert_stats_zero(cache);
    uint16_t kind;
    uint32_t table;
    for (uint32_t id = 3; id <= 15; id += 4) {
        assert(!page_cache_get(cache, id, &kind, &table, body));
    }
    memset(body, 0xb7, sizeof(body));
    page_cache_put(cache, 7, PAGE_KIND_INDEX, 29, body);
    page_cache_put(cache, 19, PAGE_KIND_INDEX, 29, body);
    assert_body_hit(cache, 7);
    assert_body_hit(cache, 19);
    assert(calls == 4 && frees == 0);
    page_cache_reset(cache);
    page_cache_reset(cache);
    assert_stats_zero(cache);
    assert(calls == 4 && frees == 0);
    page_cache_destroy(cache);
    assert(frees == 4 && live_blocks == 0);
}

static void test_body_failure_retries_only_after_reset(void)
{
    calls = frees = 0;
    fail_at = 4;
    page_cache_t *cache = page_cache_create(2);
    assert(cache != NULL && calls == 3);
    uint8_t body[PAGE_BODY_BYTES];
    memset(body, 0xb7, sizeof(body));
    page_cache_put(cache, 3, PAGE_KIND_INDEX, 29, body);
    assert(calls == 4 && live_blocks == 3);
    page_cache_put(cache, 7, PAGE_KIND_INDEX, 29, body);
    page_cache_put(cache, 11, PAGE_KIND_INDEX, 29, body);
    assert(calls == 4);
    uint16_t kind;
    uint32_t table;
    assert(!page_cache_get(cache, 3, &kind, &table, body));

    page_cache_reset(cache);
    assert_stats_zero(cache);
    assert(calls == 4 && frees == 0);
    page_cache_put(cache, 3, PAGE_KIND_INDEX, 29, body);
    assert(calls == 5 && live_blocks == 4);
    assert_body_hit(cache, 3);
    page_cache_destroy(cache);
    assert(frees == 4 && live_blocks == 0);
    fail_at = 0;
}

static void test_create_failure_releases_partial_allocations(void)
{
    for (size_t failure = 1; failure <= 3; failure++) {
        calls = frees = 0;
        fail_at = failure;
        assert(page_cache_create(2) == NULL);
        assert(live_blocks == 0);
    }
    fail_at = 0;
    calls = frees = 0;
    page_cache_reset(NULL);
    assert(calls == 0 && frees == 0);
    assert_stats_zero(NULL);
}

int main(void)
{
    setbuf(stdout, NULL);
    puts("reset invalidates collisions, drop and CLOCK without allocation");
    test_reset_invalidates_collisions_and_reuses_allocations();
    puts("body failure retries only after reset");
    test_body_failure_retries_only_after_reset();
    puts("create failures release partial allocations; NULL reset is safe");
    test_create_failure_releases_partial_allocations();
    puts("ALL PASSED: page cache fault tests");
    return 0;
}
