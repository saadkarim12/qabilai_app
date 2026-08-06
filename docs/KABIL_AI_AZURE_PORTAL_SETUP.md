# Kabil.ai — Azure Blob Storage Setup (Portal)

> Step-by-step manual setup using the Azure Portal. No CLI needed. We're provisioning private blob storage for CV PDFs; everything else (compute, Postgres, Redis) lives on Railway.

**End state:** A private storage account in UAE North with one container `kabil-cvs`, plus a connection string to drop into Railway env vars.

**Estimated cost:** ~$0-2/month at low volume. Free tier covers most of it for 12 months.

**Time to complete:** ~15 minutes.

---

## Part 1 — Sign in to the Portal

1. Open [https://portal.azure.com](https://portal.azure.com) in your browser.
2. Sign in with the account that has your Azure subscription.
3. Once you're in, you should see the Azure dashboard.

If this is a fresh free-tier account, you may see a banner about $200 free credit. That's normal.

### Check you have the right subscription active

1. In the top-right corner, click your profile icon.
2. You should see "Directory: \<your email\>" and below it "Subscription". If you have multiple, you can switch using **"Switch directory"** here.

If you only have one subscription, skip ahead.

---

## Part 2 — Create a Resource Group

A resource group is just a folder for related resources. Putting everything in one group makes cleanup easy later.

1. In the top search bar, type **"Resource groups"** and click the result.
2. Click **"+ Create"** at the top-left.
3. Fill in:
   - **Subscription:** select your Azure subscription (free tier)
   - **Resource group name:** `kabil-rg`
   - **Region:** **UAE North**
4. Click **"Review + create"** at the bottom.
5. Wait for validation, then click **"Create"**.

Wait 10-15 seconds. You'll see a notification "Resource group created" in the top-right bell icon.

---

## Part 3 — Create the Storage Account

This is the actual storage resource.

### Step 3.1 — Start the create flow

1. In the top search bar, type **"Storage accounts"** and click the result.
2. Click **"+ Create"** at the top-left.

### Step 3.2 — Basics tab

Fill in:

| Field | Value | Notes |
|---|---|---|
| Subscription | your subscription | |
| Resource group | `kabil-rg` | select from dropdown |
| Storage account name | `kabilstorage` + random digits | **must be globally unique, lowercase + digits only, 3-24 chars.** Try `kabilstorage2026` or `kabilstoragekm` — if taken, add more digits. The portal will tell you if it's taken (red error under the field). |
| Region | **UAE North** | |
| Primary service | Azure Blob Storage | |
| Performance | Standard | |
| Redundancy | **LRS (Locally redundant storage)** | cheapest, fine for v1 |

Once the name shows a green checkmark, click **"Next"** at the bottom.

> Write down your final storage account name. You'll need it later. e.g. `kabilstoragekm26`.

### Step 3.3 — Advanced tab

Most defaults are fine. Verify these specific settings:

| Setting | Value |
|---|---|
| **Require secure transfer for REST API operations** | ✅ Enabled (default) |
| **Allow enabling anonymous access on individual containers** | ❌ **UNCHECK THIS** |
| **Enable storage account key access** | ✅ Enabled (we need this for connection strings) |
| **Default to Azure Active Directory authorization in the Azure portal** | ✅ Enabled |
| **Minimum TLS version** | **Version 1.2** |
| **Access tier** | **Hot** |

The "Allow enabling anonymous access on individual containers" checkbox is the critical one — **make sure it is unchecked**. This prevents anyone from accidentally making the CV container public later.

Leave the rest as default. Click **"Next"**.

### Step 3.4 — Networking tab

Defaults are fine for v1.

- **Network access:** "Enable public access from all networks" (this is the default and means accessible from the internet via TLS+credentials — *not* anonymously public)
- **Routing preference:** Microsoft network routing (default)

Click **"Next"**.

### Step 3.5 — Data protection tab

The portal turns on soft-delete by default, which is good. Verify:

| Setting | Value |
|---|---|
| **Enable soft delete for blobs** | ✅ Enabled, **7 days** |
| **Enable soft delete for containers** | ✅ Enabled, **7 days** |
| **Enable versioning for blobs** | leave unchecked for v1 |
| **Enable blob change feed** | leave unchecked for v1 |
| **Enable point-in-time restore** | leave unchecked for v1 |

Soft delete means if you accidentally delete a CV blob, you can recover it within 7 days. Worth having.

Click **"Next"**.

### Step 3.6 — Encryption, Tags, Review

- **Encryption** tab — leave all defaults (Microsoft-managed keys). Click Next.
- **Tags** tab — optional metadata. You can add `project=kabil` if you like, or skip. Click Next.
- **Review + create** — validation runs.

If validation passes (green banner at top), click **"Create"** at the bottom.

Deployment takes 30-90 seconds. When done, you'll see "Your deployment is complete" with a green checkmark.

Click **"Go to resource"**.

---

## Part 4 — Create the Blob Container

You're now on the storage account page. We need to create one container inside it for the CVs.

1. In the left sidebar, under **Data storage**, click **"Containers"**.
2. Click **"+ Container"** at the top.
3. A panel slides in from the right:
   - **Name:** `kabil-cvs`
   - **Anonymous access level:** **Private (no anonymous access)** — confirm this is selected
4. Click **"Create"**.

The container `kabil-cvs` now appears in the list. Click on it to verify — it should be empty.

---

## Part 5 — Get the Connection String

This is the secret Railway needs.

1. Still on the storage account page, in the left sidebar under **Security + networking**, click **"Access keys"**.
2. You'll see two keys: `key1` and `key2`. (Two keys exist so you can rotate without downtime.)
3. Under **key1**, click **"Show"** next to "Connection string".
4. The full connection string appears. It looks like:

   ```
   DefaultEndpointsProtocol=https;AccountName=kabilstoragekm26;AccountKey=AbCd1234EfGh...==;EndpointSuffix=core.windows.net
   ```

5. Click the copy icon next to it.

**Treat this like a password.** Anyone with it has full read/write/delete access to the storage account. Don't paste it in chat tools, screenshots, or git commits.

---

## Part 6 — Drop it into Railway

1. Open your Railway project in the browser.
2. Click the service that will run your FastAPI backend (create a service first if you haven't yet).
3. Click the **"Variables"** tab.
4. Add two variables:

   | Key | Value |
   |---|---|
   | `AZURE_BLOB_CONNECTION_STRING` | the full string you copied |
   | `AZURE_BLOB_CONTAINER` | `kabil-cvs` |

5. Save. Railway redeploys the service automatically.

That's it on the infrastructure side. The rest is application code.

---

## Part 7 — Python code that uses it

This is the wrapper for your FastAPI backend. Drop it in your project as `src/integrations/azure_blob.py`. Add this dependency:

```
azure-storage-blob>=12.19
```

```python
# src/integrations/azure_blob.py
import hashlib
from datetime import datetime, timedelta, timezone
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.blob import generate_blob_sas, BlobSasPermissions
from src.config import settings


class AzureBlobClient:
    """Thin async wrapper around Azure Blob Storage for CV PDFs."""

    def __init__(self) -> None:
        self._client = BlobServiceClient.from_connection_string(
            settings.azure_blob_connection_string
        )
        self._container_name = settings.azure_blob_container

    async def upload_pdf(self, candidate_id: str, pdf_bytes: bytes) -> tuple[str, str]:
        """
        Upload a PDF and return (blob_path, sha256).
        Idempotent: if the same bytes already exist at this path, returns the existing path.
        """
        sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        blob_path = f"cvs/{candidate_id}/{sha256}.pdf"

        container = self._client.get_container_client(self._container_name)
        blob = container.get_blob_client(blob_path)

        if not await blob.exists():
            await blob.upload_blob(
                pdf_bytes,
                overwrite=False,
                content_settings={"content_type": "application/pdf"},
            )

        return blob_path, sha256

    async def download_pdf(self, blob_path: str) -> bytes:
        container = self._client.get_container_client(self._container_name)
        blob = container.get_blob_client(blob_path)
        stream = await blob.download_blob()
        return await stream.readall()

    def get_signed_url(self, blob_path: str, ttl_minutes: int = 15) -> str:
        """
        Generate a short-lived read URL for the FE to display a CV.
        Sync because SAS generation is local-only (no network call).
        """
        parts = dict(
            kv.split("=", 1)
            for kv in settings.azure_blob_connection_string.split(";")
            if "=" in kv
        )
        account_name = parts["AccountName"]
        account_key = parts["AccountKey"]

        sas = generate_blob_sas(
            account_name=account_name,
            container_name=self._container_name,
            blob_name=blob_path,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
        )
        return (
            f"https://{account_name}.blob.core.windows.net/"
            f"{self._container_name}/{blob_path}?{sas}"
        )

    async def delete_pdf(self, blob_path: str) -> None:
        container = self._client.get_container_client(self._container_name)
        blob = container.get_blob_client(blob_path)
        await blob.delete_blob()

    async def close(self) -> None:
        await self._client.close()
```

And the settings additions in `src/config.py`:

```python
class Settings(BaseSettings):
    # ... your other settings ...
    azure_blob_connection_string: str
    azure_blob_container: str = "kabil-cvs"
```

---

## Part 8 — Smoke test

Run this locally to verify everything is wired up before deploying to Railway. Save as `scripts/test_blob.py`:

```python
# scripts/test_blob.py
import asyncio
from src.integrations.azure_blob import AzureBlobClient


async def main() -> None:
    client = AzureBlobClient()
    try:
        fake_pdf = b"%PDF-1.4\n%fake content\n%%EOF"
        path, sha = await client.upload_pdf("test-candidate-id", fake_pdf)
        print(f"Uploaded: {path}")
        print(f"SHA256: {sha}")

        downloaded = await client.download_pdf(path)
        assert downloaded == fake_pdf, "Round-trip mismatch"
        print("Round-trip OK")

        url = client.get_signed_url(path, ttl_minutes=5)
        print(f"Signed URL: {url}")

        await client.delete_pdf(path)
        print("Deleted")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
```

Set env vars locally and run:

```bash
export AZURE_BLOB_CONNECTION_STRING="...the string you copied..."
export AZURE_BLOB_CONTAINER="kabil-cvs"
python scripts/test_blob.py
```

Expected output:

```
Uploaded: cvs/test-candidate-id/<long-hash>.pdf
SHA256: <long-hash>
Round-trip OK
Signed URL: https://kabilstoragekm26.blob.core.windows.net/...
Deleted
```

You can also verify in the portal: while the script is paused (add an `input("press enter")` before the delete), refresh the container view — you should see the blob there.

---

## Part 9 — Things you can do in the Portal later

### Browse what's in storage

Storage account → **Containers** → `kabil-cvs` — you can see blobs, download them, check sizes.

### View costs

Storage account → **Cost analysis** (under Cost Management in the left sidebar) — shows your spend by service.

### Rotate the connection string

Do this quarterly, or immediately if you suspect the key leaked.

1. Storage account → **Access keys**.
2. Click **"Rotate key"** next to **key2** first (so you can fail over).
3. Wait until the rotation completes.
4. Update Railway's `AZURE_BLOB_CONNECTION_STRING` with **key2's** new connection string.
5. Wait until Railway redeploys and your app is using the new key (~1-2 min).
6. Come back and rotate **key1**.

This way you never have downtime — `key1` keeps working while you swap to `key2`, then you rotate `key1`.

### Recover a soft-deleted blob

Storage account → **Containers** → `kabil-cvs` → toggle **"Show deleted blobs"** at the top. Click a deleted blob → **"Undelete"**. Available for 7 days after deletion.

### Set up alerts

Storage account → **Alerts** → **"+ Create alert rule"**. Useful alerts:

- Egress > 50 GB/month (cost spike or data leak)
- Failed authentication > 100/hour (someone brute-forcing keys)

### Delete everything

If you ever shut down the project: go to **Resource groups** → `kabil-rg` → **"Delete resource group"** at the top. Type the name to confirm. Everything inside is wiped.

---

## What's next

Once this is done:
- Provision Postgres on Railway (enable pgvector extension via migration)
- Provision Redis on Railway
- Wire up the FastAPI backend per Phase 0-1 of the main architecture doc
- Come back to this only if you need to rotate keys or check storage usage

---

*End of document.*