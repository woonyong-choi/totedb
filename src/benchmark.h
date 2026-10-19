#ifndef TOTE_BENCHMARK_H
#define TOTE_BENCHMARK_H

#ifdef TOTE_BENCHMARK
#include <stddef.h>
#include <stdint.h>

enum tb_bench_phase {
    TB_REQUEST, TB_PARSE, TB_EXEC, TB_METADATA, TB_PUBLISH, TB_ROW_INDEX,
    TB_HANDLE_LOCK, TB_FILE_LOCK, TB_READ, TB_WRITE, TB_SYNC, TB_LOCK_SYSCALL,
    TB_PHASE_COUNT
};
enum { TB_BENCH_DEPTH = 32, TB_BENCH_RECORDS = 64 };
typedef struct {
    uint64_t wall_ns, cpu_ns, exclusive_wall_ns, exclusive_cpu_ns;
    uint64_t attempts, bytes, errors, short_io, eintr;
} tb_bench_cost;
typedef struct {
    uint64_t sequence, pid, tid;
    uint64_t incomplete, overflow, dropped;
    char operation[32];
    tb_bench_cost phases[TB_PHASE_COUNT];
} tb_bench_record;
typedef struct { unsigned depth; int request; uint64_t sequence; } tb_bench_scope;
tb_bench_scope tb_bench_enter(enum tb_bench_phase phase, int request);
tb_bench_scope tb_bench_request(const char *operation);
void tb_bench_leave(tb_bench_scope *scope);
void tb_bench_io(enum tb_bench_phase phase, int64_t result, size_t wanted, int error);
size_t tb_bench_drain(tb_bench_record *records, size_t capacity);
#define TB_SCOPE(name, phase) tb_bench_scope name __attribute__((cleanup(tb_bench_leave))) = tb_bench_enter(phase, 0)
#define TB_REQUEST_SCOPE(name) tb_bench_scope name __attribute__((cleanup(tb_bench_leave))) = tb_bench_request(__func__)
#define TB_BEGIN(name, phase) tb_bench_scope name = tb_bench_enter(phase, 0)
#define TB_END(name) tb_bench_leave(&(name))
#define TB_IO(phase, result, wanted, error) tb_bench_io(phase, result, wanted, error)
#else
#define TB_SCOPE(name, phase)
#define TB_REQUEST_SCOPE(name)
#define TB_BEGIN(name, phase)
#define TB_END(name)
#define TB_IO(phase, result, wanted, error)
#endif
#endif
