// Offline candidate only: not imported by sections or trading workers.
// Build without fast-math; stable tie order must match the existing Numba oracle.
#include <algorithm>
#include <cstdint>
#include <vector>
#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif
extern "C" EXPORT void s3_holdings(
    const int64_t* times, const int32_t* symbols, const double* ranks,
    int64_t rows, int count, const double* previous, double enter, double leave,
    int active_start, int delay, int32_t* output) {
    std::fill(output, output + rows, 0);
    if (!rows) return;
    delay %= 11;
    const int depth = std::max(delay, 1);
    std::vector<double> held(previous, previous + count);
    std::vector<double> ring(depth * count, -1.0);
    std::vector<uint8_t> keep(count);
    struct Candidate { int64_t row; double rank; };
    std::vector<Candidate> candidates;
    candidates.reserve(count);
    int pointer = 0;
    for (int64_t begin = 0; begin < rows;) {
        int64_t end = begin + 1;
        while (end < rows && times[end] == times[begin]) ++end;
        if (times[begin] >= active_start) {
            std::fill(keep.begin(), keep.end(), 0);
            int kept = 0;
            for (auto j = begin; j < end; ++j) {
                int s = symbols[j];
                if (held[s] >= 0 && ranks[j] <= leave) { keep[s] = 1; ++kept; }
            }
            const int needed = std::max(1, static_cast<int>((end - begin) * enter)) - kept;
            std::fill(held.begin(), held.end(), -1.0);
            candidates.clear();
            for (auto j = begin; j < end; ++j) {
                int s = symbols[j];
                if (keep[s]) { output[j] = 1; held[s] = ranks[j]; }
                else if (needed > 0) {
                    double r = delay ? ring[pointer * count + s] : ranks[j];
                    if (r >= 0) candidates.push_back({j, r});
                }
            }
            if (needed > 0) {
                std::stable_sort(candidates.begin(), candidates.end(),
                    [](const Candidate& a, const Candidate& b) { return a.rank < b.rank; });
                for (int j = 0; j < std::min(needed, static_cast<int>(candidates.size())); ++j) {
                    auto row = candidates[j].row;
                    output[row] = 1; held[symbols[row]] = ranks[row];
                }
            }
        }
        std::fill(ring.begin() + pointer * count, ring.begin() + (pointer + 1) * count, -1.0);
        for (auto j = begin; j < end; ++j) ring[pointer * count + symbols[j]] = ranks[j];
        pointer = (pointer + 1) % depth;
        begin = end;
    }
}
