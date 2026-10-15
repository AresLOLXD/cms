#include <cstdio>
int main() {
    int n;
    if (scanf("%d", &n) != 1) return 0;
    int s = 0;
    for (int i = 0; i < n; i++) { int x; scanf("%d", &x); s += x; }
    printf("%d\n", s);
    return 0;
}
