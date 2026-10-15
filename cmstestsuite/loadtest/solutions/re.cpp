#include <cstdio>
#include <cstdlib>
int main() {
    int n;
    if (scanf("%d", &n) != 1) return 0;
    if (n > 1000) abort();  // crashes on the large inputs
    long long s = 0;
    for (int i = 0; i < n; i++) { long long x; scanf("%lld", &x); s += x; }
    printf("%lld\n", s);
    return 0;
}
