#include <cstdio>
#include <vector>
int main() {
    int n;
    if (scanf("%d", &n) != 1) return 0;
    std::vector<long long> v(n);
    for (int i = 0; i < n; i++) scanf("%lld", &v[i]);
    volatile long long s = 0;
    // Quadratic on purpose: fine up to n = 1000, far too slow for 1e5.
    for (int i = 0; i < n; i++)
        for (int j = 0; j < n; j++)
            if (i == j) s = s + v[j];
    printf("%lld\n", (long long)s);
    return 0;
}
