#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <time.h>
#include "../src/benchmark.h"

static unsigned clock_call;
static int fail_clock;
int tb_test_clock(clockid_t clock_id, struct timespec *out)
{
    static const long fixture[] = {100, 10, 120, 14, 150, 20, 200, 30};
    (void)clock_id;
    errno = EDOM;
    if (fail_clock) return -1;
    out->tv_sec = 0;
    out->tv_nsec = clock_call < 8 ? fixture[clock_call] : 1000 + clock_call * 10;
    clock_call++;
    return 0;
}

static void test_nested_exclusive(void)
{
    errno = EBUSY;
    tb_bench_scope request = tb_bench_enter(TB_REQUEST, 1);
    tb_bench_scope child = tb_bench_enter(TB_EXEC, 0);
    tb_bench_leave(&child);
    tb_bench_leave(&request);
    assert(errno == EBUSY);
    tb_bench_record r;
    assert(tb_bench_drain(&r, 1) == 1);
    assert(r.incomplete == 0);
    assert(r.phases[TB_REQUEST].wall_ns == 100);
    assert(r.phases[TB_REQUEST].exclusive_wall_ns == 70);
    assert(r.phases[TB_REQUEST].cpu_ns == 20);
    assert(r.phases[TB_REQUEST].exclusive_cpu_ns == 14);
    assert(r.phases[TB_EXEC].wall_ns == 30);
    assert(r.phases[TB_EXEC].cpu_ns == 6);
}

static int early_return(void)
{
    TB_REQUEST_SCOPE(request);
    return -7;
}

static void test_io_and_failures(void)
{
    assert(early_return() == -7);
    tb_bench_record records[TB_BENCH_RECORDS];
    assert(tb_bench_drain(records, TB_BENCH_RECORDS) == 1);
    tb_bench_scope request = tb_bench_enter(TB_REQUEST, 1);
    tb_bench_io(TB_READ, 3, 8, EIO);
    tb_bench_io(TB_READ, -1, 5, EINTR);
    tb_bench_io(TB_READ, 5, 5, EIO);
    tb_bench_leave(&request);
    assert(tb_bench_drain(records, TB_BENCH_RECORDS) == 1);
    tb_bench_cost c = records[0].phases[TB_READ];
    assert(c.attempts == 3 && c.bytes == 8);
    assert(c.errors == 1 && c.eintr == 1 && c.short_io == 1);
    fail_clock = 1;
    assert(early_return() == -7);
    fail_clock = 0;
    assert(tb_bench_drain(records, TB_BENCH_RECORDS) == 1);
    assert(records[0].incomplete > 0);
    request = tb_bench_enter(TB_REQUEST, 1);
    tb_bench_scope scopes[TB_BENCH_DEPTH];
    for (unsigned i = 0; i < TB_BENCH_DEPTH; i++) scopes[i] = tb_bench_enter(TB_EXEC, 0);
    for (unsigned i = TB_BENCH_DEPTH; i > 0; i--) tb_bench_leave(&scopes[i - 1]);
    tb_bench_leave(&request);
    assert(tb_bench_drain(records, TB_BENCH_RECORDS) == 1);
    assert(records[0].overflow == 1 && records[0].incomplete > 0);
    for (unsigned i = 0; i < TB_BENCH_RECORDS + 2; i++) assert(early_return() == -7);
    assert(tb_bench_drain(records, TB_BENCH_RECORDS) == TB_BENCH_RECORDS);
    uint64_t dropped = 0;
    for (unsigned i = 0; i < TB_BENCH_RECORDS; i++) dropped += records[i].dropped;
    assert(dropped == 2);
    assert(tb_bench_drain(records, TB_BENCH_RECORDS) == 0);
    assert(early_return() == -7);
    assert(tb_bench_drain(records, TB_BENCH_RECORDS) == 1);
    assert(records[0].dropped == 0);
    for (unsigned i = 0; i < TB_BENCH_RECORDS + 3; i++) assert(early_return() == -7);
    assert(tb_bench_drain(records, 0) == 0);
    assert(tb_bench_drain(records, 1) == 1);
    assert(records[0].dropped == 3);
    assert(tb_bench_drain(records, TB_BENCH_RECORDS) == TB_BENCH_RECORDS - 1);
    for (unsigned i = 0; i < TB_BENCH_RECORDS - 1; i++) assert(records[i].dropped == 0);
}

static void test_nested_requests_and_unmatched_stack(void)
{
    tb_bench_record r;
    tb_bench_scope parent = tb_bench_enter(TB_REQUEST, 1);
    tb_bench_scope child = tb_bench_enter(TB_REQUEST, 1);
    assert(tb_bench_drain(&r, 1) == 0);
    tb_bench_leave(&child);
    tb_bench_leave(&parent);
    assert(tb_bench_drain(&r, 1) == 1);
    assert(r.incomplete == 0);
    assert(r.phases[TB_REQUEST].exclusive_wall_ns == 60);
    parent = tb_bench_enter(TB_REQUEST, 1);
    child = tb_bench_enter(TB_EXEC, 0);
    tb_bench_leave(&parent);
    tb_bench_leave(&child);
    assert(tb_bench_drain(&r, 1) == 1);
    assert(r.incomplete > 0);
    assert(early_return() == -7);
    assert(tb_bench_drain(&r, 1) == 1);
    assert(r.incomplete == 0);
}

int main(void)
{
    test_nested_exclusive();
    test_io_and_failures();
    test_nested_requests_and_unmatched_stack();
    return 0;
}
