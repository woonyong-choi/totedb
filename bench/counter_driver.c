#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <sqlite3.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "totedb.h"
#include "../src/benchmark.h"

static const char *schema = "CREATE TABLE counters (id INTEGER PRIMARY KEY, counter INTEGER NOT NULL)";
static const char *update = "UPDATE counters SET counter=counter+1 WHERE id=1";
static const char *query = "SELECT counter FROM counters WHERE id=1";

typedef struct {
    uint64_t begin_ns, prepare_ns, exec_ns, commit_ns, output_ns;
    size_t completed;
    int error;
#ifdef TOTE_BENCHMARK
    tb_bench_record *records;
    size_t record_count, record_capacity;
#endif
} trial;

static uint64_t now_ns(void)
{
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t) != 0) abort();
    return (uint64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}

#ifdef TOTE_BENCHMARK
static void drain_records(trial *t)
{
    t->record_count += tb_bench_drain(t->records + t->record_count, t->record_capacity - t->record_count);
}
#define DRAIN(t) drain_records(t)
#else
#define DRAIN(t)
#endif

static uint64_t little(const uint8_t *p, unsigned n)
{
    uint64_t v = 0;
    for (unsigned i = 0; i < n; i++) v |= (uint64_t)p[i] << (i * 8);
    return v;
}

static int tote_statement(tote_db_t *db, tote_txn_t *txn, const char *sql, int64_t expected, int rows, trial *t)
{
    uint8_t *wire = NULL;
    size_t size = 0;
    uint64_t start = now_ns();
    int status = tote_exec(db, txn, sql, NULL, 0, &wire, &size);
    t->exec_ns += now_ns() - start;
    start = now_ns();
    int valid = status == TOTE_OK && size >= 5 && wire[0] == (rows ? 0 : 1);
    if (valid && rows) {
        valid = little(wire + 1, 4) == 1 && size >= 9;
        if (valid) {
            uint64_t name_size = little(wire + 5, 4);
            size_t at = 9 + (size_t)name_size + 2;
            valid = at <= size && size - at == 11 && wire[at] == 1 && wire[at + 1] == 1 &&
                    (int64_t)little(wire + at + 2, 8) == expected && wire[at + 10] == 0;
        }
    } else if (valid && expected >= 0) {
        uint64_t tag_size = little(wire + 1, 4);
        valid = tag_size <= size - 5 && size - 5 - tag_size == 8 &&
                little(wire + 5 + tag_size, 8) == (uint64_t)expected;
    }
    tote_free(wire);
    t->output_ns += now_ns() - start;
    return valid ? 0 : 1;
}

static int run_tote(const char *path, size_t n, trial *t)
{
    tote_db_t *db = NULL;
    if (tote_open(path, 1, &db) != TOTE_OK) return 1;
    if (tote_statement(db, NULL, schema, -1, 0, t) ||
        tote_statement(db, NULL, "INSERT INTO counters VALUES (1,0)", 1, 0, t)) return 2;
    DRAIN(t);
    t->exec_ns = t->output_ns = 0;
#ifdef TOTE_BENCHMARK
    t->record_count = 0;
#endif
    for (size_t i = 0; i < n; i++) {
        tote_txn_t *txn = NULL;
        uint64_t start = now_ns();
        int status = tote_begin(db, &txn);
        t->begin_ns += now_ns() - start;
        if (status != TOTE_OK) return 3;
        if (tote_statement(db, txn, update, 1, 0, t)) { tote_rollback(&txn); return 4; }
        start = now_ns();
        status = tote_commit(&txn);
        t->commit_ns += now_ns() - start;
        DRAIN(t);
        if (status != TOTE_OK) return 5;
        t->completed++;
    }
    uint64_t exec_ns = t->exec_ns, output_ns = t->output_ns;
    if (tote_statement(db, NULL, query, (int64_t)n, 1, t)) return 6;
    if (tote_close(&db) != TOTE_OK || tote_open(path, 0, &db) != TOTE_OK) return 7;
    if (tote_statement(db, NULL, query, (int64_t)n, 1, t)) return 8;
    t->exec_ns = exec_ns;
    t->output_ns = output_ns;
    return tote_close(&db) == TOTE_OK ? 0 : 9;
}

static int sqlite_value(sqlite3 *db, size_t expected)
{
    sqlite3_stmt *s = NULL;
    if (sqlite3_prepare_v2(db, query, -1, &s, NULL) != SQLITE_OK) return 1;
    int valid = sqlite3_step(s) == SQLITE_ROW && sqlite3_column_int64(s, 0) == (int64_t)expected;
    valid = valid && sqlite3_step(s) == SQLITE_DONE;
    if (sqlite3_finalize(s) != SQLITE_OK) valid = 0;
    return valid ? 0 : 1;
}

static int run_sqlite(const char *path, size_t n, int reuse, trial *t)
{
    sqlite3 *db = NULL;
    sqlite3_stmt *s = NULL;
    if (sqlite3_open(path, &db) != SQLITE_OK) return 10;
    if (sqlite3_exec(db, "PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL", NULL, NULL, NULL) != SQLITE_OK ||
        sqlite3_exec(db, schema, NULL, NULL, NULL) != SQLITE_OK ||
        sqlite3_exec(db, "INSERT INTO counters VALUES (1,0)", NULL, NULL, NULL) != SQLITE_OK) return 11;
    if (reuse && sqlite3_prepare_v2(db, update, -1, &s, NULL) != SQLITE_OK) return 12;
    for (size_t i = 0; i < n; i++) {
        uint64_t start = now_ns();
        int status = sqlite3_exec(db, "BEGIN", NULL, NULL, NULL);
        t->begin_ns += now_ns() - start;
        if (status != SQLITE_OK) return 13;
        if (!reuse) {
            start = now_ns();
            status = sqlite3_prepare_v2(db, update, -1, &s, NULL);
            t->prepare_ns += now_ns() - start;
            if (status != SQLITE_OK) return 14;
        }
        start = now_ns();
        status = sqlite3_step(s);
        t->exec_ns += now_ns() - start;
        start = now_ns();
        if (status != SQLITE_DONE || sqlite3_changes(db) != 1) return 15;
        status = reuse ? sqlite3_reset(s) : sqlite3_finalize(s);
        if (!reuse) s = NULL;
        t->output_ns += now_ns() - start;
        if (status != SQLITE_OK) return 16;
        start = now_ns();
        status = sqlite3_exec(db, "COMMIT", NULL, NULL, NULL);
        t->commit_ns += now_ns() - start;
        if (status != SQLITE_OK) return 17;
        t->completed++;
    }
    if (s != NULL && sqlite3_finalize(s) != SQLITE_OK) return 18;
    if (sqlite_value(db, n) || sqlite3_close(db) != SQLITE_OK) return 19;
    if (sqlite3_open(path, &db) != SQLITE_OK || sqlite_value(db, n)) return 20;
    return sqlite3_close(db) == SQLITE_OK ? 0 : 21;
}

static void print_trial(const trial *t)
{
    printf("{\"error\":%d,\"completed\":%zu,\"begin_ns\":%" PRIu64 ",\"prepare_ns\":%" PRIu64
           ",\"exec_ns\":%" PRIu64 ",\"commit_ns\":%" PRIu64 ",\"output_client_ns\":%" PRIu64 ",\"records\":[",
           t->error, t->completed, t->begin_ns, t->prepare_ns, t->exec_ns, t->commit_ns, t->output_ns);
#ifdef TOTE_BENCHMARK
    for (size_t i = 0; i < t->record_count; i++) {
        const tb_bench_record *r = &t->records[i];
        printf("%s{\"operation\":\"%s\",\"sequence\":%" PRIu64 ",\"pid\":%" PRIu64 ",\"tid\":%" PRIu64
               ",\"incomplete\":%" PRIu64 ",\"overflow\":%" PRIu64 ",\"dropped\":%" PRIu64 ",\"phases\":[",
               i ? "," : "", r->operation, r->sequence, r->pid, r->tid, r->incomplete, r->overflow, r->dropped);
        for (unsigned p = 0; p < TB_PHASE_COUNT; p++) {
            const tb_bench_cost *c = &r->phases[p];
            printf("%s{\"wall_ns\":%" PRIu64 ",\"cpu_ns\":%" PRIu64 ",\"exclusive_wall_ns\":%" PRIu64
                   ",\"exclusive_cpu_ns\":%" PRIu64 ",\"attempts\":%" PRIu64 ",\"bytes\":%" PRIu64
                   ",\"errors\":%" PRIu64 ",\"short_io\":%" PRIu64 ",\"eintr\":%" PRIu64 "}",
                   p ? "," : "", c->wall_ns, c->cpu_ns, c->exclusive_wall_ns, c->exclusive_cpu_ns,
                   c->attempts, c->bytes, c->errors, c->short_io, c->eintr);
        }
        printf("]}");
    }
#endif
    printf("]}\n");
}

int main(int argc, char **argv)
{
    if (argc != 4) return 64;
    char *end;
    errno = 0;
    unsigned long n = strtoul(argv[3], &end, 10);
    if (errno || *end || n == 0 || n > 100000 || access(argv[2], F_OK) == 0) return 64;
    trial t = {0};
#ifdef TOTE_BENCHMARK
    t.record_capacity = n * 3 + 8;
    t.records = calloc(t.record_capacity, sizeof(*t.records));
    if (t.records == NULL) return 70;
#endif
    if (strcmp(argv[1], "tote") == 0) t.error = run_tote(argv[2], n, &t);
    else if (strcmp(argv[1], "sqlite-each") == 0) t.error = run_sqlite(argv[2], n, 0, &t);
    else if (strcmp(argv[1], "sqlite-reuse") == 0) t.error = run_sqlite(argv[2], n, 1, &t);
    else return 64;
    print_trial(&t);
#ifdef TOTE_BENCHMARK
    free(t.records);
#endif
    return t.error ? 1 : 0;
}
