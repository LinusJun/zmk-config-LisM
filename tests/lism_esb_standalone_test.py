"""Actual ESB peripheral with standalone guards enabled and two BLE peer roles."""
from pathlib import Path
import argparse
import os
import subprocess
import tempfile
from lism_esb_peripheral_test import COMMON,EXTRA,TEST,ROOT


def main():
    p=argparse.ArgumentParser();p.add_argument('--tinycrypt',type=Path,required=True);args=p.parse_args()
    tc=args.tinycrypt.resolve()/'lib'
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp)
        extra=EXTRA.replace('(void)c;(void)i;return -1;','(void)c;i->role=1;i->state=1;return 0;')
        (tmp/'common.h').write_text(COMMON+extra+'\n#define CONFIG_LISM_STANDALONE 1\n')
        for name in ['zephyr/kernel.h','zephyr/sys/atomic.h','zephyr/bluetooth/bluetooth.h',
            'zephyr/bluetooth/conn.h','zephyr/bluetooth/gatt.h','zephyr/bluetooth/uuid.h',
            'zephyr/logging/log.h','zephyr/dt-bindings/input/input-event-codes.h',
            'zmk/split/transport/types.h','zmk/split/transport/peripheral.h',
            'zmk/events/split_peripheral_status_changed.h','esb.h']:
            f=tmp/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('#include "common.h"\n')
        # Copy production code unchanged; the sibling header replaces only the
        # unrelated mode/link I/O dependency, not any ESB control implementation.
        copied=tmp/'src/esb';copied.mkdir(parents=True)
        (copied/'peripheral.c').write_bytes((ROOT/'src/esb/peripheral.c').read_bytes())
        sibling=tmp/'src/standalone';sibling.mkdir()
        (sibling/'mode.h').write_text('''#include "common.h"
bool lism_standalone_direct(void);
bool lism_standalone_running(void);
bool lism_standalone_control_conn(struct bt_conn *);
void lism_standalone_peer_poll(struct bt_conn *);
''')
        test=TEST.replace('#include "peripheral.c"','''#include "common.h"
static struct bt_conn mock_conn;
static bool mock_direct;
bool lism_standalone_direct(void){return mock_direct;}
bool lism_standalone_running(void){return true;}
bool lism_standalone_control_conn(struct bt_conn *c){return c==&mock_conn;}
void lism_standalone_peer_poll(struct bt_conn *c){assert(c==&mock_conn);}
#include "peripheral.c"''')
        test=test.replace(' puts("PASS actual ESB peripheral:', ''' struct bt_conn host={.security=2};unsigned previous=mock_disconnects;
 assert(!atomic_get(&control_lost));
 reading=true;reading_conn=&mock_conn;
 disconnected(&host,0);assert(reading && reading_conn==&mock_conn && !atomic_get(&control_lost));
 disconnect_control(&host,NULL);assert(mock_disconnects==previous);
 disconnected(&mock_conn,0);assert(!reading && atomic_get(&control_lost));
 lism_esb_transport_poll(600);
 mock_conn.security=2;mock_direct=true;config_work_fn(NULL);assert(!reading_conn);
 mock_direct=false;config_work_fn(NULL);assert(reading && reading_conn==&mock_conn);
 disconnected(&mock_conn,0);lism_esb_transport_poll(601);
 start_session(&s,700);lism_standalone_esb_enable(false);
 ack=ack_for(&s,1,1,0);lism_esb_transport_receive(&ack,701);assert(waiting && !synced);
 assert(!lism_esb_transport_plan(&plan,701));lism_esb_transport_poll(702);assert(!online);
 puts("PASS standalone ESB: host disconnect isolation, ID0-only control discovery/disconnect, direct-mode exclusion, disabled-radio ACK rejection");
 puts("PASS actual ESB peripheral:''',1)
        (tmp/'test.c').write_text(test)
        subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function',
            '-Wno-unused-variable','-Wno-misleading-indentation','-fsanitize=address,undefined',
            '-fno-omit-frame-pointer','-g','-I'+str(tmp),'-I'+str(copied),
            '-I'+str(ROOT/'src/esb'),'-I'+str(tc/'include'),str(tmp/'test.c'),
            str(ROOT/'src/esb/protocol.c'),str(ROOT/'src/esb/codec.c'),
            *[str(tc/'source'/f) for f in ['aes_encrypt.c','ccm_mode.c','utils.c']],
            '-o',str(tmp/'test')],check=True)
        subprocess.run([str(tmp/'test')],check=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
if __name__=='__main__':main()
