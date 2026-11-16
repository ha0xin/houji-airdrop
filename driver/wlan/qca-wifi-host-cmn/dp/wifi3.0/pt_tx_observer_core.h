/* SPDX-License-Identifier: ISC
 * Passive ledger. Caller serializes all access; keys are opaque descriptor
 * addresses, never dereferenced. No reset while the module is loaded.
 */
#ifndef PT_TX_OBSERVER_CORE_H
#define PT_TX_OBSERVER_CORE_H
#define PTO_GROUPS 1024
#define PTO_WAYS 4
#define PTO_CLASSES 3
#define PTO_BINS 8
struct pto_entry { unsigned long key; u64 ns; u32 bytes; u8 cls; };
struct pto_class {
	u64 completed, bytes, delay_ns, max_ns, tries, active, highwater;
	u64 delay[PTO_BINS];
	u64 status[17]; /* 0..15, then other; raw HAL reason, not an ACK boolean */
};
struct pto_stats {
	u64 calls, accepted, rejected, admitted, overflow, duplicate;
	u64 completed, released, unmatched, active, highwater;
	struct pto_class cls[PTO_CLASSES];
};
struct pto_state { struct pto_stats s; struct pto_entry entries[PTO_GROUPS][PTO_WAYS]; };
static unsigned int pto_group(unsigned long key)
{
	return ((key >> 6) ^ (key >> 16)) & (PTO_GROUPS - 1);
}
static unsigned int pto_bucket(u64 ns)
{
	static const u64 limits[] = {1000000, 4000000, 16000000, 64000000,
				     256000000, 1024000000, 4096000000ULL};
	unsigned int i;
	for (i = 0; i < 7; i++)
		if (ns < limits[i])
			return i;
	return 7;
}
static void pto_begin(struct pto_state *p, unsigned long key, u64 now,
		      u32 bytes, u8 cls)
{
	unsigned int i, free_slot = PTO_WAYS, group = pto_group(key);
	if (cls >= PTO_CLASSES)
		cls = 2;
	for (i = 0; i < PTO_WAYS; i++) {
		if (p->entries[group][i].key == key) {
			p->s.duplicate++;
			return;
		}
		if (!p->entries[group][i].key)
			free_slot = i;
	}
	if (free_slot == PTO_WAYS) {
		p->s.overflow++;
		return;
	}
	p->entries[group][free_slot] = (struct pto_entry){key, now, bytes, cls};
	p->s.admitted++;
	p->s.active++;
	p->s.cls[cls].active++;
	if (p->s.cls[cls].active > p->s.cls[cls].highwater)
		p->s.cls[cls].highwater = p->s.cls[cls].active;
	if (p->s.active > p->s.highwater)
		p->s.highwater = p->s.active;
}
static void pto_end(struct pto_state *p, unsigned long key, u64 now,
		    int status, unsigned int tries)
{
	unsigned int i, group = pto_group(key);
	for (i = 0; i < PTO_WAYS; i++) {
		struct pto_entry *e = &p->entries[group][i];
		struct pto_class *c;
		u64 delay;
		if (e->key != key)
			continue;
		p->s.active--;
		p->s.cls[e->cls].active--;
		if (status < 0) {
			p->s.released++;
			e->key = 0;
			return;
		}
		delay = now - e->ns;
		c = &p->s.cls[e->cls];
		p->s.completed++;
		c->completed++;
		c->bytes += e->bytes;
		c->delay_ns += delay;
		c->tries += tries;
		if (delay > c->max_ns)
			c->max_ns = delay;
		c->delay[pto_bucket(delay)]++;
		c->status[status < 16 ? status : 16]++;
		e->key = 0;
		return;
	}
	/* Release after completion is expected, and is not a missed completion. */
	if (status >= 0)
		p->s.unmatched++;
}
static u64 pto_oldest(const struct pto_state *p, u64 now)
{
	unsigned int g, i;
	u64 age = 0;
	for (g = 0; g < PTO_GROUPS; g++) {
		for (i = 0; i < PTO_WAYS; i++) {
			const struct pto_entry *e = &p->entries[g][i];
			if (e->key && now - e->ns > age)
				age = now - e->ns;
		}
	}
	return age;
}
#endif
