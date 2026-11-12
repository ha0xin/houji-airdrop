from pathlib import Path
import subprocess
p=Path(__file__).parent
s=(p/'work/dp_rx.c').read_text();start=s.index('enum dp_pt_rx_msdu_result\ndp_rx_passthru_msdu_buf_deliver');end=s.index('/* Consume a list',start);fn=s[start:end]
h=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#define qdf_min(a,b) ((a)<(b)?(a):(b))
#define QDF_IS_STATUS_ERROR(x) ((x)!=0)
enum dp_pt_rx_msdu_result { DP_PT_RX_MSDU_DELIVERED, DP_PT_RX_MSDU_BAD_SHAPE, DP_PT_RX_MSDU_BAD_TLV_SIZE, DP_PT_RX_MSDU_BAD_TLV_FLAGS, DP_PT_RX_MSDU_BAD_LENGTH, DP_PT_RX_MSDU_OSIF_REJECTED, DP_PT_RX_MSDU_UNSUPPORTED_MPDU };
struct dp_soc { void *hal_soc; uint32_t rx_pkt_tlv_size; };
struct dp_vdev { int unused; };
struct skb { uint32_t len,tailroom; int nonlinear,frag,cont,start,end; struct skb *next; unsigned char data[4096]; };
typedef struct skb *qdf_nbuf_t;
static uint32_t pad,msdulen; static int done,raw,first,last,validtlv,rawreturn,lenreject;
static int freed,consumed,rawcalls,skips,sets;
#define qdf_nbuf_is_nonlinear(n) ((n)->nonlinear)
#define qdf_nbuf_is_frag(n) ((n)->frag)
#define qdf_nbuf_is_rx_chfrag_cont(n) ((n)->cont)
#define qdf_nbuf_next(n) ((n)->next)
#define qdf_nbuf_len(n) ((n)->len)
#define qdf_nbuf_tailroom(n) ((n)->tailroom)
#define qdf_nbuf_data(n) ((n)->data)
#define hal_rx_passthru_tlv_size_valid(h,l) (validtlv)
#define hal_rx_attn_msdu_done_get(h,t) (done)
#define HAL_IS_DECAP_FORMAT_RAW(h,t) (raw)
#define hal_rx_msdu_end_first_msdu_get(h,t) (first)
#define hal_rx_msdu_end_last_msdu_get(h,t) (last)
#define hal_rx_msdu_end_l3_hdr_padding_get(h,t) (pad)
#define hal_rx_msdu_start_msdu_len_get(h,t) (msdulen)
#define dp_rx_check_pkt_len(s,l) (lenreject)
#define qdf_nbuf_set_next(n,v) ((n)->next=(v))
#define qdf_nbuf_set_rx_chfrag_start(n,v) ((n)->start=(v))
#define qdf_nbuf_set_rx_chfrag_end(n,v) ((n)->end=(v))
static void qdf_nbuf_set_pktlen(qdf_nbuf_t n,uint32_t l) { sets++; n->len=l; }
static void dp_rx_skip_tlvs(struct dp_soc *s,qdf_nbuf_t n,uint32_t p) { skips++; n->len-=s->rx_pkt_tlv_size+p; }
static int dp_rx_deliver_raw_passthru(struct dp_soc *s,struct dp_vdev *v,qdf_nbuf_t n) { (void)s;(void)v; rawcalls++; assert(n->len==msdulen && n->start && n->end); if (!rawreturn) consumed++; return rawreturn; }
static void dp_rx_nbuf_free(qdf_nbuf_t n) { (void)n; freed++; }
'''
main=r'''
int main(void) {
 struct dp_soc soc={0,128}; struct dp_vdev vdev={0};
 for(int i=0;i<17;i++) {
  struct skb n={0}; n.tailroom=4096;
  pad=2;msdulen=100;done=raw=first=last=validtlv=1;rawreturn=lenreject=0;
  freed=consumed=rawcalls=skips=sets=0;uint32_t mapped=4096;
  int expected=DP_PT_RX_MSDU_DELIVERED;
  switch(i) {
  case 1:n.nonlinear=1;expected=DP_PT_RX_MSDU_BAD_SHAPE;break;
  case 2:n.frag=1;expected=DP_PT_RX_MSDU_BAD_SHAPE;break;
  case 3:n.cont=1;expected=DP_PT_RX_MSDU_BAD_SHAPE;break;
  case 4:n.next=&n;expected=DP_PT_RX_MSDU_BAD_SHAPE;break;
  case 5:validtlv=0;expected=DP_PT_RX_MSDU_BAD_TLV_SIZE;break;
  case 6:mapped=127;expected=DP_PT_RX_MSDU_BAD_TLV_SIZE;break;
  case 7:done=0;expected=DP_PT_RX_MSDU_BAD_TLV_FLAGS;break;
  case 8:raw=0;expected=DP_PT_RX_MSDU_BAD_TLV_FLAGS;break;
  case 9:first=0;expected=DP_PT_RX_MSDU_BAD_TLV_FLAGS;break;
  case 10:last=0;expected=DP_PT_RX_MSDU_BAD_TLV_FLAGS;break;
  case 11:msdulen=9;expected=DP_PT_RX_MSDU_BAD_LENGTH;break;
  case 12:mapped=200;expected=DP_PT_RX_MSDU_BAD_LENGTH;break;
  case 13:pad=UINT32_MAX;expected=DP_PT_RX_MSDU_BAD_LENGTH;break;
  case 14:lenreject=1;expected=DP_PT_RX_MSDU_BAD_LENGTH;break;
  case 15:rawreturn=1;expected=DP_PT_RX_MSDU_OSIF_REJECTED;break;
  case 16:mapped=230;break; /* exact capacity is accepted */
  }
  enum dp_pt_rx_msdu_result result=dp_rx_passthru_msdu_buf_deliver(&soc,&vdev,&n,mapped);
  assert((int)result==expected);assert(freed+consumed==1);
  if(result==DP_PT_RX_MSDU_DELIVERED) { assert(rawcalls==1 && consumed==1 && freed==0); }
  else if(result==DP_PT_RX_MSDU_OSIF_REJECTED) { assert(rawcalls==1 && freed==1 && !consumed); }
  else { assert(!rawcalls && !skips && !sets && freed==1); }
 }
 puts("PASS: 17 exact-wrapper bounds/ownership fault-injection cases");
 return 0;
}
'''
import re
h=re.sub(r'\bpad\b','fixture_pad',h)
main=re.sub(r'\bpad\b','fixture_pad',main)
(p/'wrapper-harness.c').write_text(h+fn+main)
subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-but-set-variable','-fsanitize=address,undefined',str(p/'wrapper-harness.c'),'-o',str(p/'wrapper-harness')],check=True)
subprocess.run([str(p/'wrapper-harness')],check=True)
