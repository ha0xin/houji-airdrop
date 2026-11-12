#include <errno.h>
#include <stdlib.h>
#include <limits.h>
#include <assert.h>
struct kernel_param { void *arg; };
static int kstrtouint(const char *s, unsigned b, unsigned *v) { char *e; unsigned long x; errno=0; if (*s=='-') return -EINVAL; x=strtoul(s,&e,b); if(errno||x>UINT_MAX)return -ERANGE; if(e==s||*e)return -EINVAL; *v=x;return 0; }
static int dp_pt_rx_window_set(const char *val, const struct kernel_param *kp)
{
	unsigned int window;
	int ret = kstrtouint(val, 0, &window);

	if (ret)
		return ret;
	if (window != 1 && window != 64 && window != 256)
		return -EINVAL;
	*(unsigned int *)kp->arg = window;
	return 0;
}

int main(void) { unsigned w=64; struct kernel_param p={&w}; const char *ok[]={"1","64","256"}; unsigned expected[]={1,64,256}; const char *bad[]={"0","2","63","65","255","257","1024","-1","abc","4294967296"}; for(unsigned i=0;i<3;i++){assert(!dp_pt_rx_window_set(ok[i],&p));assert(w==expected[i]);} for(unsigned i=0;i<10;i++){w=64;assert(dp_pt_rx_window_set(bad[i],&p)<0);assert(w==64);}return 0;}
