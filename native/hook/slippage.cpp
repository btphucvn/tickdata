// Slippage engine (native) — xem slippage.h.
// Doc block 'TSLP' tu Local\TDSClone_SlippageShm, tinh slippage deterministic.
#include "slippage.h"
#include <windows.h>
#include <random>
#include <cstring>

namespace {

#pragma pack(push, 1)
struct SlipParams {
    uint32_t magic;          // 'TSLP' = 0x504C5354
    uint32_t enabled;
    uint32_t mode;           // 0=default,1=latency,2=dealer,3=stddev
    uint32_t seed;
    uint32_t flags;          // bit0 limit, bit1 stop, bit2 sl, bit3 tp, bit4 reproducible
    float    customChance;
    float    favorableChance;
    float    maxFavorable;
    float    maxUnfavorable;
    float    mean;
    float    stdev;
    float    minDelayMs;
    float    maxDelayMs;
};
#pragma pack(pop)

const wchar_t* SHM_NAME = L"Local\\TDSClone_SlippageShm";
const uint32_t MAGIC_SLIP = 0x504C5354u;   // 'TSLP'

HANDLE      g_handle = nullptr;
SlipParams* g_p      = nullptr;

// RNG dung chung — seed tu params (reproducible) hoac random_device.
std::mt19937 g_rng;
bool         g_rngInit = false;

void EnsureRng()
{
    if (g_rngInit) return;
    if (g_p && (g_p->flags & (1u << 4)))   // reproducible
        g_rng.seed(g_p->seed);
    else
        g_rng.seed(std::random_device{}());
    g_rngInit = true;
}

double U(double lo, double hi)
{
    std::uniform_real_distribution<double> d(lo, hi);
    return d(g_rng);
}

} // namespace

namespace slip {

bool Open()
{
    g_handle = OpenFileMappingW(FILE_MAP_READ, FALSE, SHM_NAME);
    if (!g_handle) return false;
    g_p = static_cast<SlipParams*>(
        MapViewOfFile(g_handle, FILE_MAP_READ, 0, 0, sizeof(SlipParams)));
    if (!g_p || g_p->magic != MAGIC_SLIP) {
        Close();
        return false;
    }
    return true;
}

void Close()
{
    if (g_p)      { UnmapViewOfFile(g_p); g_p = nullptr; }
    if (g_handle) { CloseHandle(g_handle); g_handle = nullptr; }
    g_rngInit = false;
}

bool Enabled() { return g_p && g_p->enabled; }

double SlippagePoints(int orderType, double recentVolatilityPts, int isSlTp)
{
    if (!g_p || !g_p->enabled) return 0.0;
    EnsureRng();

    // Loc theo loai lenh / SL / TP (khop _order_uses_slippage trong Python).
    if (isSlTp == 1 && !(g_p->flags & (1u << 2))) return 0.0;   // SL off
    if (isSlTp == 2 && !(g_p->flags & (1u << 3))) return 0.0;   // TP off
    if ((orderType == OP_BUYLIMIT || orderType == OP_SELLLIMIT) &&
        !(g_p->flags & (1u << 0))) return 0.0;                  // limit off
    if ((orderType == OP_BUYSTOP || orderType == OP_SELLSTOP) &&
        !(g_p->flags & (1u << 1))) return 0.0;                  // stop off

    // Xac suat co slippage
    double chance = g_p->customChance;   // Python set 100 khi khong custom
    if (U(0.0, 100.0) > chance) return 0.0;

    // Do lon theo che do
    double mag = 0.0;
    switch (g_p->mode) {
    case 3: { // standard deviation
        std::normal_distribution<double> nd(g_p->mean,
                                            g_p->stdev > 0 ? g_p->stdev : 1e-9);
        mag = std::abs(nd(g_rng));
        break;
    }
    case 1: { // latency-based
        double delayMs = U(g_p->minDelayMs,
                           g_p->maxDelayMs > g_p->minDelayMs ? g_p->maxDelayMs
                                                             : g_p->minDelayMs);
        mag = recentVolatilityPts * (delayMs / 1000.0);
        break;
    }
    case 2: { // dealer-style
        double cap = g_p->maxFavorable > g_p->maxUnfavorable
                         ? g_p->maxFavorable : g_p->maxUnfavorable;
        mag = U(0.0, cap);
        break;
    }
    default: { // default: dong deu [0, maxUnfavorable] (>=5 neu chua set)
        double mx = g_p->maxUnfavorable > 0 ? g_p->maxUnfavorable : 5.0;
        mag = U(0.0, mx);
    }
    }

    // Dau (favorable/unfavorable)
    bool favorable = U(0.0, 100.0) < g_p->favorableChance;
    double sign = favorable ? -1.0 : 1.0;

    if (g_p->mode == 2) { // dealer-style: gioi han theo cap tuong ung
        double cap = favorable ? g_p->maxFavorable : g_p->maxUnfavorable;
        if (cap > 0 && mag > cap) mag = cap;
    }
    return sign * mag;
}

} // namespace slip
