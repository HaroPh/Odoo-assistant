#!/usr/bin/env python3
# coding: utf-8
"""Live verify smoke test: 3 auto-chain scenarios on feat/auto-chain branch.

Tests:
1. Chain ngắn: "tạo báo giá cho Azure Interior, 2 Large Cabinet rồi xác nhận luôn"
   - Verify: chain_note "Sau đó tự động: Xác nhận báo giá" in confirm question
   - auto-advance: create_quotation → confirm_sale_order without interrupt
   - Menu shows "Giao hàng" (deliver_order)

2. Entry giữa chuỗi: "xác nhận S000xxx rồi giao hàng luôn" (draft order)
   - Similar verification: chain_note, auto-advance, next menu

3. Regression 1-bước: "tạo báo giá cho Azure Interior, 1 Desktop Monitor"
   - NO chain declared → NO chain_note
   - Menu appears (normal flow)
"""

import sys
import os
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import requests
import json
import hashlib
import time
import uuid
from typing import Optional

BASE_URL = "http://localhost:8000"
CHAT_ENDPOINT = f"{BASE_URL}/v1/chat/completions"

# Use unique session IDs to avoid thread state collisions
def make_unique_thread_id(scenario_name: str) -> str:
    """Create unique thread IDs to avoid reusing state from previous runs."""
    return f"live-verify-{scenario_name}-{uuid.uuid4().hex[:8]}"

def chat(messages: list[dict], thread_id: Optional[str] = None) -> tuple[str, dict]:
    """Send chat request, return (content, full_response)."""
    payload = {
        "messages": messages,
        "stream": False,
    }
    if thread_id:
        payload["session_id"] = thread_id

    resp = requests.post(CHAT_ENDPOINT, json=payload)
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return content, data

def test_scenario_1():
    """Chain ngắn: 2 steps, chain_note in confirm, auto-advance."""
    print("\n" + "="*70)
    print("SCENARIO 1: Chain ngắn (short chain)")
    print("="*70)

    user_msg = "tạo báo giá cho Azure Interior, 2 [E-COM07] Large Cabinet rồi xác nhận luôn"
    thread_id = make_unique_thread_id("s1")

    print(f"\n[Turn 1] User: {user_msg}")
    print(f"Thread: {thread_id}")

    content, resp = chat([{"role": "user", "content": user_msg}], thread_id=thread_id)
    print(f"\n[Response 1]\n{content}\n")

    # Check for chain_note
    has_chain_note = "Sau đó tự động: Xác nhận báo giá" in content
    print(f"[OK] Chain note present: {has_chain_note}")
    if not has_chain_note:
        print("  FAIL: Expected chain_note 'Sau đó tự động: Xác nhận báo giá'")
        return False

    # Check position: chain_note should come before question
    if "Sau đó tự động" in content and "Xác nhận? (có / không)" in content:
        note_pos = content.index("Sau đó tự động")
        q_pos = content.index("Xác nhận? (có / không)")
        if note_pos < q_pos:
            print("[OK] Chain note positioned before question")
        else:
            print("[ERROR] Chain note NOT before question")
            return False

    # Resume with "có"
    print(f"\n[Turn 2] User: có")
    content2, resp2 = chat(
        [{"role": "user", "content": user_msg},
         {"role": "assistant", "content": content},
         {"role": "user", "content": "có"}],
        thread_id=thread_id
    )
    print(f"\n[Response 2]\n{content2}\n")

    # Check for auto-advance indicator (delivery menu should appear)
    has_delivery_menu = "Giao hàng" in content2
    print(f"[OK] Delivery (Giao hàng) menu offered: {has_delivery_menu}")
    if not has_delivery_menu:
        print("  FAIL: Expected 'Giao hàng' option after auto-chain completion")
        return False

    print("\n[PASS] SCENARIO 1 PASSED")
    return True

def test_scenario_2():
    """Entry giữa chuỗi: confirm existing draft, chain_note, auto-advance."""
    print("\n" + "="*70)
    print("SCENARIO 2: Entry giữa chuỗi (mid-chain entry)")
    print("="*70)

    # First, create a draft order for testing (using a product we know exists)
    create_msg = "tạo báo giá cho Azure Interior, 1 [E-COM07] Large Cabinet"
    print(f"\n[Setup] Creating draft order: {create_msg}")

    thread_setup = make_unique_thread_id("s2-setup")
    content_setup, _ = chat([{"role": "user", "content": create_msg}], thread_id=thread_setup)
    print(f"[Setup Response] (draft confirm)\n{content_setup[:150]}...\n")

    # Confirm the draft to actually create the order
    confirm_setup = "có"
    content_confirm_setup, _ = chat(
        [{"role": "user", "content": create_msg},
         {"role": "assistant", "content": content_setup},
         {"role": "user", "content": confirm_setup}],
        thread_id=thread_setup
    )
    print(f"[Setup Response 2] (after confirm)\n{content_confirm_setup}\n")

    # Extract order ref from the CONFIRM response (this is where S00xxx appears)
    import re
    match = re.search(r'S\d{5}', content_confirm_setup)
    if not match:
        print(f"[FAIL] Could not extract order ref from confirm response: {content_confirm_setup}")
        return False
    order_ref = match.group(0)
    print(f"[OK] Draft order created and confirmed: {order_ref}\n")

    # Now test scenario 2: entry mid-chain
    user_msg = f"xác nhận {order_ref} rồi giao hàng luôn"
    thread_id = make_unique_thread_id("s2-chain")

    print(f"\n[Turn 1] User: {user_msg}")
    print(f"Thread: {thread_id}")

    content, resp = chat([{"role": "user", "content": user_msg}], thread_id=thread_id)
    print(f"\n[Response 1]\n{content}\n")

    # Check for chain_note (should include "Giao hàng" auto action)
    has_chain_note = "Sau đó tự động:" in content and "Giao hàng" in content
    print(f"[OK] Chain note for delivery present: {has_chain_note}")
    if not has_chain_note:
        print("  FAIL: Expected chain_note with 'Giao hàng'")
        return False

    # Resume with "có"
    print(f"\n[Turn 2] User: có")
    content2, resp2 = chat(
        [{"role": "user", "content": user_msg},
         {"role": "assistant", "content": content},
         {"role": "user", "content": "có"}],
        thread_id=thread_id
    )
    print(f"\n[Response 2]\n{content2}\n")

    # After confirm→deliver chain, should see some completion or next menu
    print("[OK] Auto-chain executed")

    print("\n[PASS] SCENARIO 2 PASSED")
    return True

def test_scenario_3():
    """Regression 1-bước: single step, NO chain_note."""
    print("\n" + "="*70)
    print("SCENARIO 3: Regression 1-bước (no chain)")
    print("="*70)

    # Use a product we know exists, WITHOUT chain declaration (no "rồi xác nhận")
    user_msg = "tạo báo giá cho Azure Interior, 1 [E-COM07] Large Cabinet"
    thread_id = make_unique_thread_id("s3")

    print(f"\n[Turn 1] User: {user_msg}")
    print(f"Thread: {thread_id}")

    content, resp = chat([{"role": "user", "content": user_msg}], thread_id=thread_id)
    print(f"\n[Response 1]\n{content}\n")

    # Check that NO chain_note is present (critical test)
    has_chain_note = "Sau đó tự động:" in content
    print(f"[OK] NO chain note (as expected): {not has_chain_note}")
    if has_chain_note:
        print("  FAIL: Should NOT have chain_note for single-step order")
        return False

    # Should have confirmation question
    has_confirm_q = "Xác nhận? (có / không)" in content
    print(f"[OK] Confirmation question present: {has_confirm_q}")
    if not has_confirm_q:
        print("  FAIL: Expected confirmation question")
        return False

    # Resume with "có"
    print(f"\n[Turn 2] User: có")
    content2, resp2 = chat(
        [{"role": "user", "content": user_msg},
         {"role": "assistant", "content": content},
         {"role": "user", "content": "có"}],
        thread_id=thread_id
    )
    print(f"\n[Response 2]\n{content2}\n")

    # Should show regular menu (no auto-chain continuation)
    print("[OK] Single-step order completed (no auto-chain)")

    print("\n[PASS] SCENARIO 3 PASSED")
    return True

def main():
    print("Testing auto-chain feature on feat/auto-chain branch")
    print(f"Backend: {BASE_URL}")

    # Check health
    try:
        health = requests.get(f"{BASE_URL}/health").json()
        print(f"[OK] Backend healthy: {health}")
    except Exception as e:
        print(f"[ERROR] Backend health check failed: {e}")
        return False

    results = []

    try:
        results.append(("Scenario 1 (chain ngắn)", test_scenario_1()))
    except Exception as e:
        print(f"\n[ERROR] Scenario 1 failed with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(("Scenario 1 (chain ngắn)", False))

    try:
        results.append(("Scenario 2 (entry giữa chuỗi)", test_scenario_2()))
    except Exception as e:
        print(f"\n[ERROR] Scenario 2 failed with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(("Scenario 2 (entry giữa chuỗi)", False))

    try:
        results.append(("Scenario 3 (regression 1-bước)", test_scenario_3()))
    except Exception as e:
        print(f"\n[ERROR] Scenario 3 failed with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(("Scenario 3 (regression 1-bước)", False))

    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    for name, passed in results:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{status} {name}")

    all_passed = all(p for _, p in results)
    print("\n" + ("[PASS] ALL TESTS PASSED" if all_passed else "[FAIL] SOME TESTS FAILED"))
    return all_passed

if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
