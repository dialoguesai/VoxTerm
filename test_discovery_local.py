#!/usr/bin/env python3
"""Local discovery test — run this to verify two VoxTerm instances can see each other via mDNS.

Usage:
    Terminal 1:  python3 test_discovery_local.py alice
    Terminal 2:  python3 test_discovery_local.py bob

Both should print when they discover the other.
If run with no args, spawns both in one process (automated test).
"""

import sys
import os
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from network.discovery import PeerDiscovery


def run_peer(name, node_id, port, found_event=None, stop_event=None):
    """Run a single discoverable peer."""
    print(f"[{name}] Starting mDNS discovery (node={node_id[:8]}, port={port})...")

    disc = PeerDiscovery(node_id, name, tcp_port=port, udp_port=0)

    def on_found(peer_info):
        print(f"[{name}] FOUND PEER: {peer_info.display_name} at {peer_info.ip}:{peer_info.tcp_port} (session={peer_info.in_session})")
        if found_event:
            found_event.set()

    def on_lost(nid):
        print(f"[{name}] LOST PEER: {nid[:8]}")

    disc.on_peer_found = on_found
    disc.on_peer_lost = on_lost
    disc.start()
    disc.update_session_status(True)
    print(f"[{name}] Advertising on mDNS. Waiting for peers...")

    if stop_event:
        stop_event.wait()
    else:
        try:
            while True:
                visible = disc.get_visible_peers()
                if visible:
                    print(f"[{name}] Visible peers: {', '.join(p.display_name + ' (' + p.ip + ')' for p in visible)}")
                else:
                    print(f"[{name}] No peers visible yet...")
                time.sleep(3)
        except KeyboardInterrupt:
            pass

    disc.stop()
    print(f"[{name}] Stopped.")
    return disc


def run_automated():
    """Spawn two peers in one process and verify they find each other."""
    print("=" * 60)
    print("AUTOMATED LOCAL DISCOVERY TEST")
    print("=" * 60)
    print()

    found_a = threading.Event()
    found_b = threading.Event()
    stop = threading.Event()

    t1 = threading.Thread(
        target=run_peer,
        args=("alice", "aaaa1111aaaa1111", 19900),
        kwargs={"found_event": found_a, "stop_event": stop},
        daemon=True,
    )
    t2 = threading.Thread(
        target=run_peer,
        args=("bob", "bbbb2222bbbb2222", 19901),
        kwargs={"found_event": found_b, "stop_event": stop},
        daemon=True,
    )

    t1.start()
    time.sleep(0.5)  # stagger slightly
    t2.start()

    print()
    print("Waiting up to 10 seconds for mutual discovery...")
    print()

    a_ok = found_a.wait(timeout=10)
    b_ok = found_b.wait(timeout=10)

    print()
    print("=" * 60)
    if a_ok and b_ok:
        print("SUCCESS: Both peers discovered each other!")
    elif a_ok:
        print("PARTIAL: Alice found Bob, but Bob did NOT find Alice")
    elif b_ok:
        print("PARTIAL: Bob found Alice, but Alice did NOT find Bob")
    else:
        print("FAILURE: Neither peer discovered the other")
        print()
        print("Troubleshooting:")
        print("  - Is mDNS/Bonjour running? (macOS: always on, Linux: check avahi-daemon)")
        print("  - Is your firewall blocking UDP port 5353?")
        print("  - Try: dns-sd -B _voxterm._tcp local.")
    print("=" * 60)

    stop.set()
    time.sleep(1)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        name = sys.argv[1]
        node_id = f"{name}_{os.getpid():08d}xx"[:16]
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 19900
        run_peer(name, node_id, port)
    else:
        run_automated()
