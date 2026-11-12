#!/usr/bin/env python3
"""Copy only Samsung PASSTHRU table entries into the preserved Xiaomi tables."""
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
SAMSUNG = HERE.parents[1] / 'samsung-s928b-16-src/vendor/qcom/opensource/wlan/qcacld-3.0/components/cmn_services/policy_mgr'
BASE = HERE / 'reference/port/policy_mgr'
OUT = HERE / 'candidate/policy_mgr'

def close(text, start):
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return i
    raise ValueError('unbalanced initializer')

def nodes(text):
    # All tables use named globals and designated PM entries. Match their
    # lexical containment, preserving all non-PASSTHRU target entries.
    # Preserve offsets while ignoring examples inside comments.
    masked = re.sub(r'/\*.*?\*/|//[^\n]*', lambda m: re.sub(r'[^\n]', ' ', m[0]), text, flags=re.S)
    found = []
    for m in re.finditer(r'(?:\[(PM_\w+)\]|\b(\w+)(?:\s*\[[^\]]+\])*)\s*=\s*\{', masked):
        start = masked.index('{', m.start(), m.end())
        end = close(masked, start)
        parents = [n for n in found if n['start'] < start < n['end']]
        parent = max(parents, key=lambda n: n['start']) if parents else None
        key = (() if parent is None else parent['key']) + (m[1] or m[2],)
        found.append(dict(key=key, parent=parent, begin=m.start(), start=start, end=end))
    return found

def mobile_source(source):
    # Samsung adds an alternative MDM/AUTO set of identically named tables.
    # Xiaomi houji uses the mobile LA branch, not those alternative policies.
    mobile = '#if !defined(MDM_PLATFORM) && !defined(AUTO_PLATFORM)'
    if mobile in source:
        start = source.index(mobile)
        depth = 0
        branch_end = finish = None
        for directive in re.finditer(r'^#(if\w*|else|endif)\b[^\n]*', source[start:], re.M):
            kind = directive[1]
            if kind.startswith('if'):
                depth += 1
            elif kind == 'else' and depth == 1:
                branch_end = start + directive.start()
            elif kind == 'endif':
                depth -= 1
                if depth == 0:
                    finish = start + directive.end()
                    break
        assert branch_end and finish
        source = source[:branch_end] + source[finish:]
    return source

def port_table(source, target):
    source = mobile_source(source)
    sn, tn = ( [n for n in nodes(t) if not n['key'][0].startswith(('fourth_', 'fifth_'))]
               for t in (source, target) )
    mapping = {n['key']: n for n in tn}
    assert len(mapping) == len(tn), 'Duplicate target initializer'
    assert len({n['key'] for n in sn}) == len(sn), 'Duplicate source initializer'
    additions = {}
    count = 0
    for node in sn:
        if 'PASSTHRU' not in node['key'][-1]:
            continue
        if any('PASSTHRU' in part for part in node['key'][:-1]):
            continue
        assert node['key'] not in mapping, node['key']
        if node['parent']['key'] not in mapping:
            # A newer Samsung-only concurrency combination has no target
            # row. It must not introduce unrelated enums or policy here.
            print('Skipped absent target row:', node['parent']['key'])
            continue
        parent = mapping[node['parent']['key']]
        fragment = source[node['begin']:node['end'] + 1]
        additions.setdefault(parent['end'], []).append(fragment)
        count += 1
    for end, fragments in sorted(additions.items(), reverse=True):
        prefix = target[:end].rstrip()
        comma = '' if prefix.endswith((',', '{')) else ','
        target = prefix + comma + '\n\t' + ',\n\t'.join(fragments) + ',\n' + target[end:]
    return target, count

def main():
    total = 0
    for base in sorted((BASE / 'src').glob('wlan_policy_mgr_tables*.h')):
        if base.name == 'wlan_policy_mgr_tables_no_dbs_i.h':
            # The new admission gate rejects PT concurrency on non-DBS HW.
            # Do not touch the legacy conditional no-DBS tables.
            continue
        source = SAMSUNG / 'src' / base.name
        if not source.exists() or 'PASSTHRU' not in source.read_text():
            continue
        result, count = port_table(source.read_text(), base.read_text())
        if count:
            out = OUT / 'src' / base.name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(result)
            print(base.name, count)
            total += count
    print('Imported PASSTHRU entries:', total)
    # Enum values are appended; existing policy indices retain their values.
    header = (BASE / 'inc/wlan_policy_mgr_public_struct.h').read_text()
    header = header.replace('\tPM_MAX_NUM_OF_MODE\n', '\tPM_PASSTHRU_MODE,\n\tPM_MAX_NUM_OF_MODE\n')
    header = header.replace('\tPM_MAX_ONE_CONNECTION_MODE\n', '\tPM_PASSTHRU_24_1x1_2x2,\n\tPM_PASSTHRU_5_1x1_2x2,\n\tPM_MAX_ONE_CONNECTION_MODE\n')
    (OUT / 'inc').mkdir(parents=True, exist_ok=True)
    (OUT / 'inc/wlan_policy_mgr_public_struct.h').write_text(header)

    utils = (BASE / 'src/wlan_policy_mgr_get_set_utils.c').read_text()
    for function in ('policy_mgr_set_concurrency_mode', 'policy_mgr_clear_concurrency_mode',
                     'policy_mgr_incr_active_session', 'policy_mgr_decr_active_session'):
        utils = edit_function(utils, function, lambda s: replace_once(s, '\tcase QDF_STA_MODE:', '\tcase QDF_PASSTHRU_MODE:\n\tcase QDF_STA_MODE:'))
    utils = edit_function(utils, 'policy_mgr_incr_active_session', lambda s: replace_once(s,
        '\tpolicy_mgr_incr_connection_count(psoc, session_id, mode);', '''
	if (mode == QDF_PASSTHRU_MODE) {
		if (QDF_IS_STATUS_ERROR(policy_mgr_incr_connection_count(psoc, session_id, mode))) {
			pm_ctx->no_of_active_sessions[mode]--;
			qdf_mutex_release(&pm_ctx->qdf_conc_list_lock);
			if (pm_ctx->dp_cbacks.hdd_v2_flow_pool_unmap)
				pm_ctx->dp_cbacks.hdd_v2_flow_pool_unmap(session_id);
			return;
		}
	} else {
		policy_mgr_incr_connection_count(psoc, session_id, mode);
	}'''))
    utils = edit_function(utils, 'policy_mgr_qdf_opmode_to_pm_con_mode', lambda s: replace_once(s,
        '\tcase QDF_STA_MODE:', '\tcase QDF_PASSTHRU_MODE:\n\t\tmode = PM_PASSTHRU_MODE;\n\t\tbreak;\n\tcase QDF_STA_MODE:'))
    utils = edit_function(utils, 'policy_mgr_get_qdf_mode_from_pm', lambda s: replace_once(s,
        '\tcase PM_STA_MODE:', '\tcase PM_PASSTHRU_MODE:\n\t\tmode = QDF_PASSTHRU_MODE;\n\t\tbreak;\n\tcase PM_STA_MODE:'))
    samsung_utils = (SAMSUNG / 'src/wlan_policy_mgr_get_set_utils.c').read_text()
    begin = samsung_utils.index('#ifdef DRIVER_PASSTHRU_MODE\nstatic bool\npolicy_mgr_allow_passthru_concurrency')
    end = samsung_utils.index('#else', begin)
    helper = samsung_utils[begin:end].replace('#ifdef DRIVER_PASSTHRU_MODE\n', '')
    helper = helper.replace('policy_mgr_2_freq_same_mac_in_dbs(psoc,',
                            'policy_mgr_2_freq_same_mac_in_dbs(policy_mgr_get_context(psoc),')
    pos = utils.index('bool policy_mgr_is_concurrency_allowed(')
    utils = utils[:pos] + helper + '\n' + utils[pos:]
    utils = edit_function(utils, 'policy_mgr_is_concurrency_allowed', lambda s: replace_once(s,
        '\tif (ch_freq) {', '\tif (ch_freq) {\n\t\tif (!policy_mgr_allow_passthru_concurrency(psoc, mode, ch_freq, pcl))\n\t\t\treturn false;\n'))
    # Explicitly fail closed where no-DBS tables have no PT entries. Also
    # restrict this first implementation to one ordinary STA plus one PT.
    utils = edit_function(utils, 'policy_mgr_allow_concurrency', lambda s: replace_once(s,
        '\tqdf_mem_zero(&pcl, sizeof(pcl));', '''
	if (mode == PM_PASSTHRU_MODE && policy_mgr_get_connection_count(psoc)) {
		if (!policy_mgr_is_hw_dbs_capable(psoc) ||
		    policy_mgr_get_connection_count(psoc) != 1 ||
		    policy_mgr_mode_specific_connection_count(psoc, PM_STA_MODE, NULL) != 1)
			return false;
	}
	if (policy_mgr_mode_specific_connection_count(psoc, PM_PASSTHRU_MODE, NULL) &&
	    policy_mgr_get_connection_count(psoc) >= 2)
		return false;
	qdf_mem_zero(&pcl, sizeof(pcl));'''))
    (OUT / 'src/wlan_policy_mgr_get_set_utils.c').write_text(utils)

    pcl = (BASE / 'src/wlan_policy_mgr_pcl.c').read_text()
    pcl = replace_once(pcl, '\t[PM_STA_MODE] = {PM_NONE, PM_NONE, PM_NONE},',
                       '\t[PM_PASSTHRU_MODE] = {PM_NONE, PM_NONE, PM_NONE},\n\t[PM_STA_MODE] = {PM_NONE, PM_NONE, PM_NONE},')
    pcl = edit_function(pcl, 'policy_mgr_get_second_connection_pcl_table_index', lambda s: replace_once(s,
        '\t} else if (PM_LL_LT_SAP_MODE == pm_conc_connection_list[0].mode) {', '''
	} else if (PM_PASSTHRU_MODE == pm_conc_connection_list[0].mode) {
		index = WLAN_REG_IS_24GHZ_CH_FREQ(pm_conc_connection_list[0].freq) ?
			PM_PASSTHRU_24_1x1_2x2 : PM_PASSTHRU_5_1x1_2x2;
	} else if (PM_LL_LT_SAP_MODE == pm_conc_connection_list[0].mode) {'''))
    (OUT / 'src/wlan_policy_mgr_pcl.c').write_text(pcl)

def replace_once(text, old, new):
    assert text.count(old) == 1, (old, text.count(old))
    return text.replace(old, new)

def edit_function(text, name, edit):
    match = re.search(r'^[ \t]*(?:[A-Za-z_][A-Za-z_0-9]*[ *]+)*' + name + r'\s*\([^;{}]*\)\s*\{', text, re.M)
    assert match, name
    start = text.index('{', match.start(), match.end())
    end = close(text, start)
    return text[:start] + edit(text[start:end + 1]) + text[end + 1:]

if __name__ == '__main__':
    main()
