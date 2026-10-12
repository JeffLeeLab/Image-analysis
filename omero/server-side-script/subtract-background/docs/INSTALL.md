# Installing OMERO background subtraction 0.1.0

This guide installs the script on a **Linux OMERO scripting processor host**. You need both filesystem administration and an OMERO administrator account, which you have. If the processor runs in a container, the application, worker environment, config and persistent journal paths must exist **inside that container**. Installing only on the OMERO.web host or your laptop is insufficient.

The implementation has passed local checks with OMERO.py 5.23.0, Ice 3.6.5 and a real isolated Python 3.11.13 worker. It has not yet been exercised against your server. Scientific validation of the unchanged algorithm is accepted from your RDI-calculator project; there is no request to repeat that work.

## 1. Identify the actual processor runtime

Find the OS user and Python interpreter used by the scripting processor. Do not assume they are the same as your interactive shell, OMERO.web or the Java server launcher.

If uncertain, use the optional trusted diagnostic through your existing OMERO CLI:

```sh
omero login
omero script upload --official scripts/Inspect_BGSub_Runtime.py
omero script list
omero script launch DIAGNOSTIC_SCRIPT_ID
```

Run upload from the extracted release directory. Replace `DIAGNOSTIC_SCRIPT_ID` with the returned ID. The diagnostic reports the processor's Python executable, OS UID, platform and package versions, without reading image pixels or dumping environment secrets. You can also launch it from OMERO.web. Use the normal login prompt; do not put passwords in commands.

Record:

- Processor host/container and service account (examples below use `omero`).
- Processor Python absolute path (example `/opt/omero/venv/bin/python`).
- OMERO.server, OMERO.py, Python and Ice versions.
- A small test image ID, writable output dataset ID and active group.

The processor Python needs its existing `omero`, `Ice` and `numpy` packages. No SciPy is required there. If a prerequisite is absent, stop and resolve the processor installation with its operator; this guide does not install packages into that environment. The adapter requires Python 3.9+; the dedicated worker uses Python 3.11.13.

## 2. Copy and extract the release

From the development checkout, the release can be rebuilt with:

```sh
python3 tools/build_release.py
```

Transfer `dist/omero-bgsub-0.1.0.tar.gz` and its `.sha256` file to the processor host using your normal secure file-transfer mechanism. In the directory containing them:

```sh
sha256sum -c omero-bgsub-0.1.0.tar.gz.sha256
sudo install -d -m 0755 /opt/omero-bgsub/0.1.0
sudo tar -xzf omero-bgsub-0.1.0.tar.gz --strip-components=1 -C /opt/omero-bgsub/0.1.0
cd /opt/omero-bgsub/0.1.0
sha256sum -c SHA256SUMS
sudo chown -R root:root /opt/omero-bgsub/0.1.0
sudo chmod -R a+rX /opt/omero-bgsub/0.1.0
```

Use a fresh release directory. Do not overwrite a release serving active jobs. The default entry-point paths already target `/opt/omero-bgsub/0.1.0/adapter` and `/etc/omero-bgsub/0.1.0.json`. If you choose different paths, edit `ADAPTER_DIRECTORY` and `OPERATOR_CONFIG` at the top of the entry-point file before upload. These are operator settings, not GUI inputs.

## 3. Provision only the isolated worker

Use an administrator-installed `uv` executable following your institution's software-installation policy; [official uv installation instructions](https://docs.astral.sh/uv/getting-started/installation/) are available if needed. Set the following to its actual absolute path:

```sh
UV_BIN=/usr/local/bin/uv
sudo install -d -m 0755 /opt/omero-bgsub/python /var/cache/omero-bgsub-uv
sudo env UV_PYTHON_INSTALL_DIR=/opt/omero-bgsub/python \
  UV_CACHE_DIR=/var/cache/omero-bgsub-uv \
  "$UV_BIN" sync --project /opt/omero-bgsub/0.1.0/worker \
  --locked --no-dev --no-editable
sudo chmod -R a+rX /opt/omero-bgsub/python /opt/omero-bgsub/0.1.0/worker
```

The explicit Python installation directory avoids a venv pointing into root's private home. uv uses the supplied `.python-version` and `uv.lock`; NumPy 2.2.6 and SciPy 1.15.3 are isolated here. The deployed `.venv` is in the external release, not in your development repository or OMERO installation. Native wheels must exist for your Linux/architecture; deployment errors must be resolved here, not during image jobs.

Do **not** change `omero.launcher.python`. Do not activate this worker environment for the OMERO server, replace OMERO's interpreter, install SciPy into OMERO's environment or use `uv run` to resolve dependencies during jobs.

## 4. Create writable state directories and configure limits

Adjust the account/group names if your processor runs under another identity:

```sh
OMERO_SERVICE_USER=omero
OMERO_SERVICE_GROUP=omero
sudo install -d -m 0700 -o "$OMERO_SERVICE_USER" -g "$OMERO_SERVICE_GROUP" \
  /var/lib/omero-bgsub/journals /var/lib/omero-bgsub/tmp
sudo install -d -m 0755 /etc/omero-bgsub
sudo install -m 0644 config/operator.example.json /etc/omero-bgsub/0.1.0.json
sudoedit /etc/omero-bgsub/0.1.0.json
```

Review every example limit. They are initial bounded defaults, **not measured claims about your hardware or dataset**:

| Setting | Example | Meaning |
|---|---:|---|
| `max_concurrent_jobs` | 1 | One job per journal/lock directory; larger values are not supported in this release |
| `max_images_per_batch` | 100 | Matched source image limit |
| `max_planes_per_batch` | 5,000 | Sum of Z × C × T |
| `max_xy_plane_pixels` | 4,194,304 | Maximum whole-plane area; check server/client Ice message limits too |
| `max_radius_pixels` | 200 | Maximum requested radius |
| `max_output_bytes` | 20 GiB | Uncompressed output allowance per run, not a live server quota check |
| `max_name_length` | 255 | Confirm against the installed OMERO model |
| `max_batch_seconds` | 14,400 | Monotonic run budget; includes local planning time |
| `worker_timeout_seconds` | 300 | Maximum wait for one worker reply/startup |
| `worker_memory_bytes` | 2 GiB | Linux worker address-space limit, not combined wrapper+worker RSS |
| `wrapper_memory_bytes` | 2 GiB | Linux script-wrapper address-space limit; does not alter the processor service |
| `min_temporary_free_bytes` | 1 GiB | Minimum free space before staging each plane |

The two process address-space caps bound different processes (4 GiB combined in this example); they are not measured RSS estimates. The script does not query physical OMERO repository free space. Use the processor's service/container memory policy and monitor repository space. Configure CPU/thread/cgroup limits at the service level if needed. Do not set a service memory limit so low that existing OMERO scripts are killed. Start with small images before increasing limits.

The journal directory contains private user/run metadata and must persist across process/service restarts. Keep it separate from temporary arrays and include it in the operational backup/retention policy.

**Multiple processors:** install identical application/config paths on every host eligible to execute this trusted script. Use a genuinely shared journal directory with working POSIX `flock` and atomic rename/fsync semantics, or route this script to one processor host. Separate host-local directories do not provide global admission or cross-host resume. A distributed job queue is not implemented. Container restarts require persistent mounts for journals.

## 5. Check imports and worker access as the service account

Set the interpreter reported by the diagnostic:

```sh
OMERO_PROCESSOR_PYTHON=/opt/omero/venv/bin/python
sudo -u "$OMERO_SERVICE_USER" "$OMERO_PROCESSOR_PYTHON" \
  /opt/omero-bgsub/0.1.0/tools/check_installation.py \
  --config /etc/omero-bgsub/0.1.0.json
```

Expected output ends with `imports, writable paths, lock, isolated worker and zero-copy IPC OK`. This does not connect to OMERO or create images. It checks the worker package/hash, paths, temporary file exchange and zero-radius copying under the correct OS account. Service/container mount and security policies must allow the same paths when launched by the processor.

For the supplied focused local integration checks, optionally run:

```sh
sudo -u "$OMERO_SERVICE_USER" env \
  BGSUB_WORKER_PYTHON=/opt/omero-bgsub/0.1.0/worker/.venv/bin/python \
  "$OMERO_PROCESSOR_PYTHON" -m unittest discover \
  -s /opt/omero-bgsub/0.1.0/tests -v
```

These check the adapter and worker protocol; they do not repeat ImageJ comparison tests or replace the next live check.

## 6. Upload the trusted script

Using the established OMERO CLI environment, log in as your OMERO administrator and upload the configured entry point:

```sh
cd /opt/omero-bgsub/0.1.0
omero login
omero script upload --official scripts/Batch_Rolling_Ball_Background.py
omero script list
omero script params SCRIPT_ID
```

Replace `SCRIPT_ID` with the returned ID. Keep a record of it. The params command should list 28 inputs, including eight channel/radius pairs. Radius parameters are optional with minimum 0 and **no default**, preserving absent versus zero.

Uploading only the `.py` file is insufficient: the adapter and worker installed above must be available on the processor. No normal server-wide restart or Python-launcher change is part of this installation. If the installed system caches script metadata differently, follow its version-specific refresh procedure and recheck `script params`.

The CLI commands were checked against OMERO.py 5.23.0; use `omero script upload --help`, `params --help` and `replace --help` on a different installed version. See the [official trusted-script guide](https://omero.readthedocs.io/en/stable/developers/scripts/user-guide.html#script-writing-as-admin).

## 7. First live run

Use a small image in the intended group and an explicit writable test dataset. Start with Preview in OMERO.web and verify the channel inventory, destination and planned output name. Enter every channel's radius; use zero for all channels for the first copy check. Run Process and confirm the new image dimensions, channels, name, dataset link and representative pixels.

A CLI example for a **one-channel** input is below; replace the numeric values with real IDs. For multiple channels, supply the additional channel/radius pairs rather than copying this example unchanged:

```sh
omero script launch SCRIPT_ID \
  Data_Type=Image 'IDs=[123]' Run_Mode=Preview \
  Channel_Match_Mode=Index Rule_1_Channel=0 Rule_1_Radius=0 \
  Output_Mode=Existing_Dataset Output_Dataset_ID=456
```

Repeat with `Run_Mode=Process` only after Preview is correct. For a new dataset, replace destination arguments with `Output_Mode=New_Dataset 'New_Dataset_Name=Background corrected'`; omit the dataset ID. An optional `Output_Project_ID` is only valid for New_Dataset.

Then use a representative positive-radius image. This establishes the newly written OMERO I/O path; it is not a repeat of your algorithm validation. Test with the intended ordinary user's permissions before making the script generally available.

Check your deployment's actual script/session timeouts. The configured batch budget does not extend OMERO sessions automatically. A blocking OMERO RPC is governed by Ice/server timeout settings; the script checks its deadline between operations and enforces the worker timeout independently.

## 8. Logs, cancellation and recovery

Outputs include Run_ID, Message, dataset ID/link and downloadable reports. The durable journal is `<journal_root>/<run_id>.json`. It records the exact input snapshot, output IDs, stages and errors. If reporting fails or the processor is killed, use that journal plus OMERO script stdout/stderr and processor logs.

Normal resume is through `Resume_Run_ID` with the same parameters, original user and group. A new-dataset run still submits its original new-name parameters; the stored numeric ID is reused. The script checks completed pixels/metadata and finishes tracked incomplete objects without guessing by image name. It never deletes data automatically.

If creation returned an uncertain result, resume first searches the run/source description marker under the original user/group. If it finds exactly one object, it records and reuses that ID. If it finds none or multiple, it stops. An administrator must inspect server logs and objects **after the original operation has settled**:

- Dataset marker: `OMERO bgsub dataset; run=<run_id>`.
- Image marker: `OMERO bgsub image; run=<run_id>; source=<source_id>`.
- If one correct object exists, restore its numeric `destination_id`/row `output_id` in a backed-up journal only after checking ownership, group and marker.
- If server evidence confirms no object was created, clear only the corresponding `dataset_create_intent` or row `create_intent` flag while no job holds the admission lock, then resume. Do not clear flags merely because a query returned nothing during a timeout.
- Multiple matches, edited completed outputs, removed links or changed source metadata need manual reconciliation; do not blindly rerun creation or delete original inputs.

Retain the original journal before any operator edit. Stop/finish the running job before journal maintenance; locks are released automatically when processes exit. A lingering `processing.lock` file alone does not mean a job is running and should not be deleted to bypass a held lock.

SIGTERM/SIGINT trigger cleanup where possible. On Linux the worker receives a parent-death SIGKILL if its script parent is killed; it also exits when the control pipe closes. Abrupt termination can leave private `bgsub-*` temporary directories and unlinked incomplete images. Remove only confirmed abandoned temporary directories; never purge journals or OMERO objects as a generic cleanup step. Preserve completed outputs.

## 9. Upgrade and rollback

1. Stop admitting new jobs and allow active jobs to finish; retain reports/journals.
2. Install a new immutable release path and versioned config; keep the previous worker environment.
3. Update the two entry-point deployment paths and run the installation check as the service account.
4. Replace the existing trusted script using the existing ID:

   ```sh
   omero script replace SCRIPT_ID /opt/omero-bgsub/NEW_VERSION/scripts/Batch_Rolling_Ball_Background.py
   omero script params SCRIPT_ID
   ```

5. Perform a small Preview/Process check. To roll back, replace the uploaded script with the prior configured entry point and retain its original external paths.

A run records its application version. Do not resume a run under a different version or modify a live environment in place. Use the original release to recover its runs. Keep completed OMERO images and durable journals through rollback.
