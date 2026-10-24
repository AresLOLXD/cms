#include <bits/stdc++.h>
// Correct, and it burns CPU: an optional last input line says how many
// iterations. Three independent multiply chains (4 cycles per iteration) are
// about 1.1 ns per iteration on a Zen 3 and slow down by about 1.45x when the
// SMT sibling runs the same loop, a plausible stand-in for real contestant
// code. A volatile counter was too erratic (0.27 to 1 s for the same count).
int main() {
    int n;
    if (scanf("%d", &n) != 1) return 0;
    long long s = 0;
    for (int i = 0; i < n; i++) { long long x; scanf("%lld", &x); s += x; }
    long long reps = 0;
    if (scanf("%lld", &reps) != 1) reps = 0;
    unsigned long long a = 1, b = 2, c = 3;
    for (long long i = 0; i < reps; i++) {
        a = a * 6364136223846793005ULL + 1442695040888963407ULL;
        b = b * 6364136223846793005ULL + 1442695040888963407ULL;
        c = c * 6364136223846793005ULL + 1442695040888963407ULL;
    }
    volatile unsigned long long sink = a ^ b ^ c;  // keeps the loop alive
    (void)sink;
    printf("%lld\n", s);
    return 0;
}
