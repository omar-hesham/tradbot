import asyncio
import httpx
import sys
import os

async def test_scanner():
    print("Testing AI Portfolio Scanner API...")
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            # 1. Trigger Scanner
            print("Requesting AI Scan (this may take a few seconds)...")
            r = await client.post("http://127.0.0.1:8005/api/market/ai-scanner")
            if r.status_code == 200:
                print(f"Scan successful: {r.json()}")
            else:
                print(f"Scan failed: {r.status_code} - {r.text}")
                return

            # 2. Verify Recommendations List
            print("Fetching Recommendations...")
            r = await client.get("http://127.0.0.1:8005/api/market/ai-recommendations")
            recs = r.json().get("recommendations", [])
            print(f"Found {len(recs)} recommendations in database.")
            for rec in recs:
                print(f"- {rec['symbol']}: ${rec['allocation_usd']} ({rec['sentiment']})")
                print(f"  Reason: {rec['reason']}")

            if len(recs) > 0:
                print("\n[OK] SUCCESS: AI Portfolio Scanner working correctly.")
            else:
                print("\n[FAIL] No recommendations found.")

        except Exception as e:
            print(f"Error during test: {e}")

if __name__ == "__main__":
    asyncio.run(test_scanner())
