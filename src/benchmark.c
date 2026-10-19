#define _POSIX_C_SOURCE 200809L
#include "benchmark.h"
#include <errno.h>
#include <pthread.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

typedef struct {
    enum tb_bench_phase phase;
    uint64_t wall, cpu, child_wall, child_cpu;
    int valid;
} frame;
static _Thread_local struct {
    frame stack[TB_BENCH_DEPTH];
    unsigned depth, requests;
    uint64_t sequence, dropped;
    size_t count;
    tb_bench_record current, records[TB_BENCH_RECORDS];
} tb_bench_tls;

static int sample(uint64_t *wall, uint64_t *cpu)
{
    struct timespec w, c;
    if (clock_gettime(CLOCK_MONOTONIC, &w) != 0 ||
        clock_gettime(CLOCK_THREAD_CPUTIME_ID, &c) != 0) return 0;
    *wall = (uint64_t)w.tv_sec * 1000000000 + w.tv_nsec;
    *cpu = (uint64_t)c.tv_sec * 1000000000 + c.tv_nsec;
    return 1;
}

tb_bench_scope tb_bench_enter(enum tb_bench_phase phase, int request)
{
    int saved = errno;
    if (request && tb_bench_tls.requests++ == 0) {
        tb_bench_tls.depth = 0;
        memset(&tb_bench_tls.current, 0, sizeof(tb_bench_tls.current));
        tb_bench_tls.current.sequence = ++tb_bench_tls.sequence;
        tb_bench_tls.current.pid = (uint64_t)getpid();
        tb_bench_tls.current.tid = (uint64_t)(uintptr_t)pthread_self();
    }
    tb_bench_scope scope = {0, request, tb_bench_tls.sequence};
    if (tb_bench_tls.requests) {
        scope.depth = ++tb_bench_tls.depth;
        if (scope.depth <= TB_BENCH_DEPTH) {
            frame *f = &tb_bench_tls.stack[scope.depth - 1];
            *f = (frame){.phase = phase};
            f->valid = sample(&f->wall, &f->cpu);
            if (!f->valid) tb_bench_tls.current.incomplete++;
        } else {
            tb_bench_tls.current.incomplete++;
            tb_bench_tls.current.overflow++;
        }
    }
    errno = saved;
    return scope;
}

tb_bench_scope tb_bench_request(const char *operation)
{
    int saved = errno;
    tb_bench_scope scope = tb_bench_enter(TB_REQUEST, 1);
    if (tb_bench_tls.requests == 1) {
        size_t n = strlen(operation);
        if (n >= sizeof(tb_bench_tls.current.operation)) n = sizeof(tb_bench_tls.current.operation) - 1;
        memcpy(tb_bench_tls.current.operation, operation, n);
    }
    errno = saved;
    return scope;
}

void tb_bench_leave(tb_bench_scope *scope)
{
    int saved = errno;
    if (!scope->depth) { errno = saved; return; }
    if (!tb_bench_tls.requests || scope->sequence != tb_bench_tls.sequence) {
        scope->depth = 0;
        errno = saved;
        return;
    }
    if (scope->depth != tb_bench_tls.depth) {
        tb_bench_tls.current.incomplete++;
    } else if (scope->depth <= TB_BENCH_DEPTH) {
        frame *f = &tb_bench_tls.stack[scope->depth - 1];
        uint64_t wall, cpu;
        if (sample(&wall, &cpu) && f->valid && wall >= f->wall && cpu >= f->cpu) {
            wall -= f->wall;
            cpu -= f->cpu;
            tb_bench_cost *cost = &tb_bench_tls.current.phases[f->phase];
            cost->wall_ns += wall;
            cost->cpu_ns += cpu;
            if (wall >= f->child_wall && cpu >= f->child_cpu) {
                cost->exclusive_wall_ns += wall - f->child_wall;
                cost->exclusive_cpu_ns += cpu - f->child_cpu;
            } else tb_bench_tls.current.incomplete++;
            if (scope->depth > 1) {
                frame *parent = &tb_bench_tls.stack[scope->depth - 2];
                parent->child_wall += wall;
                parent->child_cpu += cpu;
            }
        } else tb_bench_tls.current.incomplete++;
    }
    tb_bench_tls.depth = scope->depth - 1;
    if (scope->request && --tb_bench_tls.requests == 0) {
        if (tb_bench_tls.count < TB_BENCH_RECORDS) {
            tb_bench_tls.current.dropped = tb_bench_tls.dropped;
            tb_bench_tls.dropped = 0;
            tb_bench_tls.records[tb_bench_tls.count++] = tb_bench_tls.current;
        } else tb_bench_tls.dropped++;
    }
    scope->depth = 0;
    errno = saved;
}

void tb_bench_io(enum tb_bench_phase phase, int64_t result, size_t wanted, int error)
{
    int saved = errno;
    if (tb_bench_tls.requests) {
        tb_bench_cost *c = &tb_bench_tls.current.phases[phase];
        c->attempts++;
        if (result > 0) c->bytes += (uint64_t)result;
        if (result < 0) { c->errors++; if (error == EINTR) c->eintr++; }
        else if ((uint64_t)result < wanted) c->short_io++;
    }
    errno = saved;
}

size_t tb_bench_drain(tb_bench_record *records, size_t capacity)
{
    int saved = errno;
    if (tb_bench_tls.requests || records == NULL) return 0;
    size_t n = capacity < tb_bench_tls.count ? capacity : tb_bench_tls.count;
    memcpy(records, tb_bench_tls.records, n * sizeof(*records));
    tb_bench_tls.count -= n;
    memmove(tb_bench_tls.records, tb_bench_tls.records + n,
            tb_bench_tls.count * sizeof(*records));
    errno = saved;
    return n;
}
