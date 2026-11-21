#ifndef LQ_RING_SIZE
#define LQ_RING_SIZE (1u << 20)
#endif
struct lq_ring {
	uint64_t total;
	char bytes[LQ_RING_SIZE];
};
static inline void lq_append(struct lq_ring *r, const char *s, unsigned n)
{
	uint64_t at = r->total;
	r->total += n;
	if (n > LQ_RING_SIZE) {
		unsigned skip = n - LQ_RING_SIZE;
		s += skip;
		at += skip;
		n = LQ_RING_SIZE;
	}
	for (unsigned i = 0; i < n; i++) {
		r->bytes[(at + i) % LQ_RING_SIZE] = s[i];
	}
}
static inline unsigned lq_copy(const struct lq_ring *r, uint64_t *pos, char *out,
    unsigned max)
{
	if (*pos > r->total) *pos = 0;
	if (r->total - *pos > LQ_RING_SIZE) *pos = r->total - LQ_RING_SIZE;
	uint64_t left = r->total - *pos;
	unsigned n = left < max ? (unsigned)left : max;
	for (unsigned i = 0; i < n; i++) {
		out[i] = r->bytes[(*pos + i) % LQ_RING_SIZE];
	}
	*pos += n;
	return n;
}
