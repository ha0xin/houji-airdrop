from pathlib import Path
import subprocess
p=Path(__file__).parent;s=(p/'work/dp_rx_err.c').read_text();start=s.index('static bool dp_rx_err_try_passthru_msdu_buf');end=s.index('\n#endif',start);fn=s[start:end]
h=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#define MAX_RXDESC_POOLS 4
#define MAX_PDEV_CNT 3
#define DP_MOD_ID_RX_ERR 4
#define HAL_MPDU_F_FRAGMENT (1U<<20)
#define wlan_op_mode_passthru 7
#define QDF_IS_STATUS_ERROR(x) ((x)!=0)
enum dp_pt_rx_msdu_result { DP_PT_RX_MSDU_DELIVERED, DP_PT_RX_MSDU_BAD_SHAPE, DP_PT_RX_MSDU_BAD_TLV_SIZE, DP_PT_RX_MSDU_BAD_TLV_FLAGS, DP_PT_RX_MSDU_BAD_LENGTH, DP_PT_RX_MSDU_OSIF_REJECTED, DP_PT_RX_MSDU_UNSUPPORTED_MPDU };
typedef int atomic_t;
#define atomic_inc(p) (++*(p))
#define atomic_set(p,v) (*(p)=(v))
static bool pt_rx_msdu_buf;
static atomic_t pt_rx_msdu_seen,pt_rx_msdu_miss,pt_rx_msdu_bad_desc,pt_rx_msdu_reaped,pt_rx_msdu_delivered,pt_rx_msdu_dropped,pt_rx_msdu_last_drop;
struct skb { uint64_t paddr; };typedef struct skb *qdf_nbuf_t;
struct rx_desc_pool {uint32_t buf_size;};
struct dp_pdev {void *free_list_head,*free_list_tail;};
struct dp_vdev {int opmode;struct dp_pdev *pdev;};
struct dp_txrx_peer {struct dp_vdev *vdev;};
struct dp_rx_desc {qdf_nbuf_t nbuf;unsigned pool_id;bool in_use,unmapped;};
struct dp_soc {void *hal_soc;struct rx_desc_pool rx_desc_buf[MAX_RXDESC_POOLS];};
struct hal_buf_info {uint64_t paddr;uint32_t sw_cookie;};
struct hal_rx_mpdu_desc_info {unsigned msdu_count,mpdu_flags,peer_meta_data;};
typedef void *dp_txrx_ref_handle;typedef void *hal_ring_desc_t;
static struct dp_txrx_peer peer_fixture;static struct dp_rx_desc desc_fixture;
static struct dp_pdev *pdev_fixture;static struct hal_buf_info hbi_fixture;
static int nopeer,nodesc,ref_fail,delivery_result;
static int peer_get,peer_put,vdev_get,vdev_put,unmaps,reclaims,consumes,frees,locked,delivers;
static uint16_t dp_rx_peer_metadata_peer_id_get(struct dp_soc *s,uint32_t m){(void)s;return m;}
static struct dp_txrx_peer *dp_tgt_txrx_peer_get_ref_by_id(struct dp_soc *s,uint16_t id,dp_txrx_ref_handle *r,int mod){(void)s;(void)id;(void)mod;if(nopeer)return NULL;peer_get++;*r=&peer_fixture;return &peer_fixture;}
static int dp_vdev_get_ref(struct dp_soc *s,struct dp_vdev *v,int mod){(void)s;(void)v;(void)mod;if(ref_fail)return 1;vdev_get++;return 0;}
static void dp_txrx_peer_unref_delete(dp_txrx_ref_handle r,int m){(void)r;(void)m;peer_put++;}
static void dp_vdev_unref_delete(struct dp_soc *s,struct dp_vdev *v,int m){(void)s;(void)v;(void)m;vdev_put++;}
static void hal_rx_reo_buf_paddr_get(void *h,hal_ring_desc_t d,struct hal_buf_info *b){(void)h;(void)d;*b=hbi_fixture;}
static struct dp_rx_desc *dp_rx_cookie_2_va_rxdma_buf(struct dp_soc *s,uint32_t c){(void)s;(void)c;return nodesc?NULL:&desc_fixture;}
static uint64_t qdf_nbuf_get_frag_paddr(qdf_nbuf_t n,int i){(void)i;return n->paddr;}
static struct dp_pdev *dp_get_pdev_for_lmac_id(struct dp_soc *s,unsigned p){(void)s;assert(p<MAX_PDEV_CNT);return pdev_fixture;}
static unsigned hal_rx_ret_buf_manager_get(void *h,hal_ring_desc_t r){(void)h;(void)r;return 0;}
static void dp_rx_err_ring_record_entry(struct dp_soc *s,uint64_t p,uint32_t c,unsigned r){(void)s;(void)p;(void)c;(void)r;}
static void dp_ipa_rx_buf_smmu_mapping_lock(struct dp_soc *s){(void)s;assert(!locked);locked=1;}
static void dp_ipa_rx_buf_smmu_mapping_unlock(struct dp_soc *s){(void)s;assert(locked);locked=0;}
static void dp_rx_nbuf_unmap_pool(struct dp_soc *s,struct rx_desc_pool *p,qdf_nbuf_t n){(void)s;(void)p;assert(locked && !desc_fixture.unmapped && n);unmaps++;}
static void dp_rx_add_to_free_desc_list(void **h,void **t,struct dp_rx_desc *d){(void)h;(void)t;assert(!locked && d->unmapped);reclaims++;d->nbuf=NULL;d->in_use=0;d->pool_id=255;}
static void dp_rx_nbuf_free(qdf_nbuf_t n){assert(n);frees++;consumes++;}
static enum dp_pt_rx_msdu_result dp_rx_passthru_msdu_buf_deliver(struct dp_soc *s,struct dp_vdev *v,qdf_nbuf_t n,uint32_t size){(void)s;(void)v;assert(n && size==4096 && !locked);delivers++;consumes++;return delivery_result;}
'''
main=r'''
int main(void){
 for(int i=0;i<16;i++){
  struct dp_pdev pdev={0},other={0};struct dp_vdev vdev={7,&pdev};struct skb n={1234};struct dp_soc soc={0};soc.rx_desc_buf[0].buf_size=4096;
  struct hal_rx_mpdu_desc_info info={1,0,23};int lmac=-99;
  peer_fixture.vdev=&vdev;pdev_fixture=&pdev;desc_fixture=(struct dp_rx_desc){&n,0,1,0};hbi_fixture=(struct hal_buf_info){1234,1};
  pt_rx_msdu_buf=true;nopeer=nodesc=ref_fail=delivery_result=0;
  peer_get=peer_put=vdev_get=vdev_put=unmaps=reclaims=consumes=frees=locked=delivers=0;
  pt_rx_msdu_seen=pt_rx_msdu_miss=pt_rx_msdu_bad_desc=pt_rx_msdu_reaped=pt_rx_msdu_delivered=pt_rx_msdu_dropped=pt_rx_msdu_last_drop=0;
  bool expect=false;
  switch(i){
  case 0:pt_rx_msdu_buf=false;break;
  case 1:nopeer=1;break;
  case 2:vdev.opmode=1;break;
  case 3:ref_fail=1;break;
  case 4:nodesc=1;break;
  case 5:desc_fixture.nbuf=NULL;break;
  case 6:desc_fixture.in_use=0;break;
  case 7:desc_fixture.pool_id=4;break;
  case 8:desc_fixture.pool_id=3;break;
  case 9:n.paddr=999;break;
  case 10:pdev_fixture=&other;break;
  case 11:desc_fixture.unmapped=1;break;
  case 12:expect=true;break;
  case 13:expect=true;info.msdu_count=2;break;
  case 14:expect=true;info.mpdu_flags=HAL_MPDU_F_FRAGMENT;break;
  case 15:expect=true;delivery_result=DP_PT_RX_MSDU_BAD_LENGTH;break;
  }
  bool ret=dp_rx_err_try_passthru_msdu_buf(&soc,NULL,&info,&lmac);
  assert(ret==expect);assert(peer_get==peer_put && vdev_get==vdev_put && !locked);
  if(expect){assert(unmaps==1 && reclaims==1 && consumes==1 && lmac==0 && pt_rx_msdu_reaped==1);assert(pt_rx_msdu_delivered+pt_rx_msdu_dropped==1);}
  else {assert(unmaps==0 && reclaims==0 && consumes==0 && delivers==0);}
 }
 puts("PASS: 16 exact-handler reference/DMA/free-list ownership cases");return 0;
}
'''
(p/'handler-harness.c').write_text(h+fn+main)
subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'handler-harness.c'),'-o',str(p/'handler-harness')],check=True)
subprocess.run([str(p/'handler-harness')],check=True)
