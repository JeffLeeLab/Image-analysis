# OMERO server-side batch rolling-ball background subtraction

Implementation specification and AI-agent handover • revised 12 October 2026

**Status:** owner decisions consolidated; version 0.1.0 is implemented and locally checked. Deployment and live integration against the university server remain to be performed. See [installation guide](docs/INSTALL.md) for commands and current operational limits. This document is the authoritative v1 contract. `spec-review.md` records the earlier review and is historical where it differs from this revision. The owner confirms that scientific validation has already been completed in the RDI-calculator project. Accept that validation for the unchanged pinned algorithm; do not repeat the scientific validation matrix. Operator facts remain outstanding for deployment, not for starting implementation.

## 0. Agreed scope and outstanding deployment facts

### 0.1 V1 contract

| Area | Required behaviour |
|---|---|
| Execution | Trusted custom script on the institutional OMERO scripting processor |
| Input | One or more image IDs, **or exactly one dataset ID**, in one active OMERO group |
| Interface | Standard OMERO script dialog with metadata Preview and Process modes |
| Channels | Exact stored channel names or explicit **zero-based** indices; complete rules for all channels |
| Batch consistency | Identical channel count and ordered channel identity across the matched batch; X/Y/Z/T sizes may vary |
| Radius | Per-channel radius in XY pixels; **0 skips subtraction and copies that channel unchanged** |
| All radii zero | Create an explicit copy with the same dimensions, pixel type and values |
| Algorithm | Supplied, pinned rolling-ball port; bright foreground, dark background, presmoothing on, automatic shrink, corrected negatives clipped to zero |
| Pixel types | uint8, uint16 and float32; preserve each source image's pixel type |
| Destination | One explicitly selected output dataset for all outputs: existing numeric ID or new dataset created from a user-entered name |
| Naming | Fixed `{basename}_bgs-{r0}-{r1}-…-{rCminus1}.tif`; no user-configurable suffix/template |
| Validation | Any metadata/configuration preflight error fails the batch before dataset/image creation |
| Runtime failures | Continue after an isolated image failure; stop on systemic session/storage/capacity failures; report partial completion |
| Repeated runs | Distinct run UUID and new outputs; explicit resume uses the original run and input snapshot |
| Python | Existing OMERO wrapper runtime plus dedicated uv numerical worker; do not install numerical dependencies into or replace OMERO's environment |
| Compute | One image and one working plane at a time per job; operator sets installation-wide admission and resource limits |
| Output scope | Ordinary OMERO multichannel images; original pixels retained; no TIFF export, custom web UI or plate/well reconstruction in v1 |
| Metadata | Preserve the fields specified in section 8; no automatic ROI, measurement, tag or attachment duplication |

“No processing” for a channel does not mean removing it from the output. A zero radius is a valid value, not an unset rule. Rules, policy choices and defaults below must agree with this table.

### 0.2 Facts needed before staging/production

The operator must provide:

1. OMERO.server, OMERO.web, `omero-py`, Ice and processor Python versions; OS/architecture, service/container topology and processor hosts.
2. Processor service account, interpreter, startup configuration, deployment access and log locations. OMERO script-upload privileges alone do not establish host access.
3. Approved external deployment paths for worker releases, temporary arrays and durable run journals; retention and maintenance owner.
4. CPU, memory, temporary storage, repository capacity/quota policy, maximum batch/plane/radius limits and job admission mechanism.
5. Script/session/process timeouts, cancellation behaviour and whether jobs/resumes can execute on different hosts.
6. Writable test group/dataset, optional project, non-admin test account and representative source images.
7. Typical/largest X/Y/Z/C/T sizes, populated channel labels, supported image types and available storage.
8. Any available RDI-calculator validation reference may be recorded for provenance; supplying or reproducing it is not a prerequisite to coding.

Credentials must use the institution's normal access mechanism and must not be stored here. Pure planner and worker development can proceed before these facts are available. Claims of validated deployment cannot.

### 0.3 Planning configuration

This describes the implementation contract; it is not a configuration file already understood by OMERO. Null operator values must be resolved before deployment.

```yaml
gui: standard_script_dialog
input: image_ids_or_single_dataset_id
channel_matching_default: exact_name       # alternative: zero_based_index
channel_layout: strict_count_and_order
spatial_and_time_dimensions: may_vary
channel_rules: require_exact_coverage
preflight_policy: fail_batch
pixel_validation: runtime_per_plane
runtime_image_errors: continue_and_report
radius_units: xy_pixels
presmooth: true
light_background: false
shrink: automatic
clip_corrected_negatives: true
output_pixel_type: preserve_source
all_zero_image: create_copy
output_destination: explicit_single_dataset
output_name: fixed_basename_bgs_ordered_radii_tif
python_deployment: omero_wrapper_and_uv_worker
images_per_job_in_parallel: 1
max_concurrent_jobs: null
max_job_memory_bytes: null
max_images_per_batch: null
max_planes_per_batch: null
max_xy_plane_pixels: null
max_radius_pixels: null
max_batch_elapsed_seconds: null
worker_python_path: null
journal_root: null
export_ome_tiff: false
scientific_validation: owner_confirmed_in_RDI_calculator
repeat_scientific_validation: false
```

## 1. Deliverables and foundations

Build `Batch_Rolling_Ball_Background.py`, an OMERO-facing adapter and a versioned numerical worker package. Deliver a pinned uv environment, focused checks of the new integration, example parameters, and installation, user, recovery and rollback runbooks. Do not recreate the RDI-calculator scientific validation suite. No source pixels are overwritten, existing results removed or server-wide configuration changed by a job.

Use `scripts.client(...)` and `BlitzGateway(client_obj=client)` with the launching user's session. Close services/session in `finally`. Use raw plane reads such as `getPrimaryPixels().getPlane(z, c, t)` and prototype `createImageFromNumpySeq(...)` against the installed API. Verify type support, plane ordering and partial-failure behaviour before depending on the convenience method. Do not read rendered previews as scientific input.

Official scripts demonstrate the `Data_Type` plus `IDs` selection convention and explicit dataset linking. They do not establish automatic output placement. Useful pinned examples are [Combine_Images.py](https://github.com/ome/omero-scripts/blob/5b4d76862a7141257c9ff221ff7f009f7ec416c0/omero/util_scripts/Combine_Images.py) and [Images_From_ROIs.py](https://github.com/ome/omero-scripts/blob/5b4d76862a7141257c9ff221ff7f009f7ec416c0/omero/util_scripts/Images_From_ROIs.py). This script always uses its explicitly resolved destination, regardless of source parents.

References labelled `stable` can change. Record deployed versions and consult matching API source while implementing. An uploaded script does not automatically provision dependencies or make sibling helper files importable.

## 2. Scientific processing contract

### 2.1 Algorithm identity

Use the owner's [commit-pinned imagej_rolling_ball.py](https://github.com/JeffLeeLab/Image-analysis/blob/e6360e0a7e04580ac30e143281edae66660f9ec6/people/AlfredoCastello/Zhiling-Marko-HIV/src/imagej_rolling_ball.py). Record its hash and attribution; review licensing/provenance when packaging. Do not fetch mutable source at runtime or substitute another rolling-ball library.

The supplied implementation uses NumPy/SciPy and float64 working arrays. `rolling_ball_background(...)` estimates the background; `subtract_background_image(image, background)` returns corrected float64 values and the fraction clipped below zero. The owner has confirmed prior scientific validation in RDI-calculator; this implementation accepts that result and preserves the pinned algorithm unchanged. No new ImageJ comparison campaign is required. Do not broaden parity claims beyond that prior validation or silently change morphology. Any proposed algorithm change must be identified separately from the OMERO integration.

### 2.2 Plane processing

- Preserve each source's X/Y/Z/C/T sizes, channel order and pixel type. Process all Z and T positions independently for every channel.
- Validate every radius: finite, nonnegative and within the operator's tested limit. Positive fractional radii are allowed. Missing values are errors.
- Branch explicitly on `radius == 0` before calling the algorithm, which rejects zero. Copy bypassed planes without smoothing, subtraction, clipping or intensity conversion. Supported finite float32 negatives remain unchanged in bypassed channels.
- For positive radii, estimate background with presmoothing on and automatic shrink; subtract from the original raw plane and clip corrected negatives to zero.
- Radius is in XY pixels. Anisotropic physical pixel sizes do not convert the ball into a physical sphere. Report differing physical scales.
- No 3D morphology, cross-time processing, projection, resampling, light-background mode or user shrink override in v1.
- Reject unsupported pixel types, packed/multi-sample RGB and complex data. Reject NaN/Inf values even in bypassed planes; do not repair them silently.
- All-zero configurations create complete copies with provenance; they are never skipped as no-ops.

### 2.3 Stored dtype

For corrected uint8/uint16 planes, compute in float64, clip to the type range, round nonnegative values half-up (`floor(clipped + 0.5)`), then cast once. Do not use truncation or ties-to-even rounding. For corrected float32 planes, apply clipping in float64, cast once and check range/finite results. Never normalize intensities.

OMERO uses one pixel type across an image's channels. V1 preserves the source type across every channel; there is no output-type selector. Bypassed channels must round-trip exactly. Clipping corrected float negatives is this workflow's policy, not a universal ImageJ behaviour.

## 3. Selection and preflight

### 3.1 Input contract

Use conventional `Data_Type = Image | Dataset` and a required list of Long `IDs`:

- Image: at least one positive image ID; deduplicate repeated IDs.
- Dataset: exactly one positive dataset ID; reject multiple entries, including repeated entries, with a clear message.
- Never infer the object type from numeric IDs or combine dataset and image IDs in one request.

Resolve objects under the user's active group. Reject missing, inaccessible, deleted or cross-group objects without exposing unauthorized information. V1 accepts ordinary microscopy images, including orphan image inputs because the destination is explicit; reject plate/well-associated inputs until separately supported.

For Dataset, enumerate all accessible child images with pagination/iterators as needed. Apply the name filter, deduplicate by image ID, sort by ID and freeze the resulting input snapshot **before any output writes**. Image mode applies the same filter to only the requested images. Source parent links are provenance only and never select output destinations.

An empty dataset or no matches produces a clear report and no dataset/image creation. Freeze selection even when the destination is the input dataset. Previous derived images already present at the start are ordinary candidates if selected or matched: v1 does not silently exclude them. Preview/user help must warn that selecting a dataset containing previous outputs can process those outputs again. Names and provenance identify the actual source of each result.

### 3.2 Name filter

`Name_Filter` defaults to empty (include all candidates). Match the complete stored image name including extensions. `Name_Match_Mode` supports Contains (literal substring), Exact (full equality) and Wildcard (full-name match; only `*` and `?` are special, all other characters literal). `Case_Sensitive` defaults true; false uses Unicode casefold on both name and pattern. `%` and `_` are literal characters. Regex is outside v1.

### 3.3 Planning and validation boundaries

Build a serialisable plan containing input snapshot, source associations, dimensions/types/group, ordered channel identities, resolved radii, output names, destination request and resource estimates. Validate configuration, metadata, permissions and limits for the entire matched batch before creating any dataset/image. One invalid matched image fails metadata preflight for the batch; no skip-invalid mode.

Preview inspects metadata only and creates no dataset, corrected image or image annotation. A downloadable preview report is an intentional OMERO file write. With no channel rules, Preview is inventory-only and marks processing validation incomplete; partial/invalid rules report validation errors.

Process repeats preflight rather than trusting an earlier Preview. Metadata-only preflight cannot detect bad pixels or unreadable planes: validate these at runtime and apply the per-image/systemic failure policy in section 9. Recheck critical metadata before processing each image. No promise is made that all runtime failures are detected before the first output exists.

## 4. Channel rules and layout consistency

`Channel_Match_Mode = Name | Index` applies globally to all rule rows. Every source channel must have exactly one rule and every rule must resolve to a channel in every matched image. Missing/extra channels, duplicate rule keys and missing radii fail preflight. There is no implicit radius or unconfigured-channel bypass.

### 4.1 Name mode

Match stored channel names after trimming outer whitespace, case-sensitively. Preserve original names in output metadata/reports. Do not guess aliases from wavelengths, fluorophores, colours or gateway fallback labels. Blank or duplicate normalized names require Index mode.

Require the same ordered normalized channel-name list in every matched image, using the lowest image ID as the reference layout. Name matching does not permit reordered channels in this strict batch contract.

For rules `DAPI -> 0`, `GFP -> 50`, `Cy5 -> 100`:

| Ordered channels | Result |
|---|---|
| DAPI, GFP, Cy5 throughout batch | Valid; copy DAPI, correct GFP/Cy5 |
| GFP, DAPI, Cy5 in one image | Reject batch for changed order |
| DAPI, GFP | Reject missing channel/rule target |
| DAPI, GFP, Cy5, Brightfield | Reject unconfigured extra channel |
| DAPI, GFP, GFP | Reject ambiguous name mapping |

### 4.2 Index mode

Selectors are integer strings `0` through `C-1`, matching zero-based API indices; there is no one-based conversion. The string `0` is a populated selector, not a disabled row. Reject negative/out-of-range/noninteger selectors.

Require the same C and ordered normalized stored-name list (including blanks) across the batch. Blank/duplicate labels are allowed here because indices address channels unambiguously. Where labels cannot establish biological identity, explicitly report that equal count/labels cannot prove consistent acquisition order; the user must supply consistently acquired data. Do not infer identity from display colour.

### 4.3 Dimensions

Only channel count/order must match across images. X/Y/Z/T sizes may differ within tested limits; each result preserves its own source sizes. Physical calibration may also differ and must be reported/preserved. This does not relax channel coverage or order requirements.

## 5. Standard OMERO dialog

Use fixed grouped rule rows and metadata Preview. Start with eight rows; this is a form limit, not an OMERO channel limit. Reject a batch requiring more rules with an actionable message; never discard excess channels. Extending the declared row count is a future configuration/deployment change.

| Parameter | Type/default | Meaning |
|---|---|---|
| `Data_Type` | String dropdown, Image | Image or Dataset |
| `IDs` | Required List of Long | One/multiple image IDs, or exactly one dataset ID |
| `Run_Mode` | String dropdown, Preview | Preview or Process |
| `Name_Filter` | Optional String, blank | Filter selected candidates |
| `Name_Match_Mode` | Dropdown, Contains | Contains, Exact, Wildcard |
| `Case_Sensitive` | Bool, true | Name-filter case handling |
| `Channel_Match_Mode` | Dropdown, Name | Exact Name or zero-based Index |
| `Rule_1_Channel` … `Rule_8_Channel` | Optional String | Channel name or index; blank disables the row |
| `Rule_1_Radius` … `Rule_8_Radius` | Optional Float/Double, minimum 0; no default | Radius in pixels; **0 skips subtraction and copies the channel unchanged** |
| `Output_Mode` | Dropdown, Existing_Dataset | Existing_Dataset or New_Dataset |
| `Output_Dataset_ID` | Optional Long | Required only for Existing_Dataset |
| `New_Dataset_Name` | Optional String | Required only for New_Dataset; OMERO assigns the new ID |
| `Output_Project_ID` | Optional Long | New_Dataset only; blank creates a dataset without a project link |
| `Resume_Run_ID` | Optional String | Explicit resume under section 9 |

Display this help beside radius inputs and in the script description: **“Set radius to 0 to skip background subtraction for that channel. The channel is kept unchanged in the output image. If all radii are 0, a copy of the image is created.”**

Do not expose suffix/naming templates, scientific policy switches, Python paths or resource controls. Fixed processing settings and the generated naming convention must appear in help/Preview.

A populated channel requires an explicitly supplied radius. Blank channel plus supplied radius is an invalid incomplete row. A fully blank row is disabled. Verify optional absence versus numeric zero in the installed client; add a row-enable checkbox if needed to preserve this distinction. Disabled rows must not carry effective rules.

The standard dialog does not promise dynamically generated channel inputs or visual pixel previews. Parameter discovery must not scan datasets, allocate planes or launch a worker. Validate actual grouping, selection population and result links in the deployed OMERO.web client.

User sequence: select input, Preview inventory, enter complete channel rules and destination, Preview the full plan, then Process with the same settings. The client may not retain form values; document actual behaviour. Preview shows names, zero-based indices, radii, layouts, per-image dimensions, fixed output names, destination and validation failures. A new dataset has no ID until Process creates it.

## 6. Destination and fixed naming

### 6.1 One explicit destination per run

Every completed output must be linked to the single resolved output dataset. No automatic source-parent placement, per-source splitting, name-based dataset lookup or orphan successful output is allowed.

- **Existing_Dataset:** require a positive `Output_Dataset_ID` and validate existence, active group and link permissions. New dataset name/project fields must be absent. The user may explicitly choose the input dataset.
- **New_Dataset:** require a nonblank `New_Dataset_Name` after outer-whitespace trimming; `Output_Dataset_ID` must be absent. Create a new dataset even if another dataset has the same name. OMERO assigns the ID; users cannot choose a new numeric ID. Optionally link it to the explicitly supplied writable project in the active group. If project is blank, leave the dataset unlinked to a project; never infer one from source parents.

For New_Dataset, validate naming constraints and create/project-link permissions during preflight. Create once only after successful full metadata preflight with at least one matched image, record its ID durably, and return it in results/reports. If creation/linking fails, stop the run. Resume reuses the recorded dataset ID instead of creating another. An empty dataset left by runtime failures is retained and reported; do not delete automatically.

### 6.2 Image name algorithm (not configurable)

For C channels, the exact form is:

```text
{basename}_bgs-{r0}-{r1}-...-{rCminus1}.tif
```

Here `r0` is the radius resolved for channel index 0, regardless of rule-row order or Name/Index mode. Include every channel, including zeros. Do not insert literal channel labels or `ch0` into the name.

1. Take the full stored source image name, not a filesystem path. Remove a terminal `.ome.tiff`, `.ome.tif`, `.tiff` or `.tif` case-insensitively, testing longest suffix first. For other names, remove only the final dot suffix when the dot is neither the first nor last character; otherwise keep the full name. Preserve the remaining characters verbatim. Reject an empty resulting basename.
2. Canonicalize each validated radius from its numeric value: shortest round-trip decimal representation, expand scientific notation, remove fractional trailing zeros and an unnecessary decimal point; normalize negative zero to `0`. Use `.` regardless of locale. Thus `50.0` becomes `50`, `12.50` becomes `12.5`, and `1e2` becomes `100`. The canonical numeric values also drive processing/fingerprints, not just labels.
3. Append `_bgs-`, the ordered tokens separated by `-`, and lowercase `.tif`.
4. Validate against the installed OMERO name-length limit during preflight. Reject overlong names with a clear reason; do not silently truncate the basename/radii or append variable suffixes.

Examples for three channels:

| Source name | Resolved radii for channels 0, 1, 2 | Output name |
|---|---|---|
| `image_001.ome.tif` | 0, 50, 100 | `image_001_bgs-0-50-100.tif` |
| `sample.tiff` | 12.5, 0, 100 | `sample_bgs-12.5-0-100.tif` |
| `sample.vsi` | 0, 0, 0 | `sample_bgs-0-0-0.tif` |
| `field.001.tif` | 0, 50, 100 | `field.001_bgs-0-50-100.tif` |

Identical source basenames/settings or repeated runs may produce identical image names. Keep the convention unchanged: never overwrite, deduplicate by name or add collision counters/UUIDs. OMERO IDs and provenance distinguish outputs; reports must include source/output IDs. Reprocessing a derived image treats its entire current basename as the source basename and appends another `_bgs-...` suffix.

The `.tif` suffix is an OMERO image label. API-created images do not thereby have an original downloadable TIFF file. V1 creates browsable OMERO pixels, not an exported/imported TIFF artifact. Explain this in user documentation.

## 7. Dedicated uv worker deployment

### 7.1 Selected architecture

Keep parameter declaration, selection, planning, OMERO reads/writes and reports in a thin adapter using the established processor runtime. Run the pinned numerical package in a separate uv environment under an administrator-configured absolute Python path. The worker never connects to OMERO and receives no session credentials; it requires NumPy/SciPy and the algorithm, not OMERO/Ice.

Do not change `omero.launcher.python`, install worker dependencies into the existing OMERO environment or rely on a per-script shebang override. The documented launcher is shared by Python scripts using that configuration. This worker architecture is an engineering integration to validate, not a built-in OMERO per-script uv facility.

The existing runtime must already support its OMERO/NumPy plane I/O. Verify this prerequisite before staging; do not silently install packages to satisfy it. Keep SciPy/algorithm imports out of wrapper support modules and parameter discovery. Make the uploaded adapter self-contained or deploy its dependency-free support code at an explicit read-only application path; do not assume uploaded siblings are importable.

The repository's Miniforge `omero` environment remains the local CLI/development environment. Provision the dedicated worker outside this repository on the processor host; no environment is created inside the repository and no existing environment is modified by this task.

### 7.2 Reproducible releases

Pin worker Python, direct/transitive dependencies, algorithm commit/hash and package version using `pyproject.toml` and `uv.lock`. In an external release directory, deploy with `uv sync --locked --no-dev --no-editable`, verify imports and a worker invocation as the service account, then invoke that release's interpreter by absolute path during jobs. Do not use runtime package installation, dependency solving, mutable downloads or a `uv run` invocation that syncs/relocks during processing.

Record wrapper OMERO/Ice/Python versions separately from worker Python/NumPy/SciPy versions. Use immutable release paths and retain previous releases for rollback; do not mutate an environment used by active jobs. Confirm launch-time environment differences and native-library loading under the real processor account.

### 7.3 Worker protocol

Exchange one plane or a small bounded set through a unique job directory using `.npy` with pickle disabled and a versioned JSON manifest. Validate returned dtype, shape, finite values and statistics before upload. Keep arrays off stdout; bound diagnostics. Prefer a persistent worker per image/job with a bounded queue, not a new interpreter per plane or full-stack staging.

Invoke a fixed executable using argument lists and `shell=False`. Sanitize inherited Python import paths; do not pass user-controlled executables or filesystem roots. Enforce deadlines, reap owned child processes on cancellation, and clean temporary arrays in `finally`. Crash recovery must handle abandoned owned directories because kill signals can bypass cleanup. Keep durable journals outside temporary directories.

## 8. Image creation, metadata and provenance

Generate planes lazily in the exact order required by the installed creation API; prototype with distinct values at each Z/C/T coordinate. Do not assume NumPy axis order. Verify shape, dtype, byte order and memory layout.

Create/track outputs before presenting them as complete. If the convenience method hides an incomplete ID or links too early, use a lower-level adapter that records the created ID before upload. Write and verify pixels/metadata, add provenance, then link to the resolved dataset and mark COMPLETE. These are not assumed atomic; recovery must reconcile interruption at every stage. Source objects are never edited.

### 8.1 Metadata contract

Required when present and supported in the source: X/Y/Z physical sizes and units; stored channel names, excitation/emission wavelengths and fluorophore labels; channel colours and active-channel choices; Pixels time increment and units; per-plane delta-T, exposure time and XYZ positions with units and correct Z/C/T coordinates. Preserve missing fields as missing. Recalculate rendering windows for corrected channels; do not modify the source rendering settings.

The staging adapter must establish which of these fields are available through the installed model/API. A required present field that cannot be transferred is a documented compatibility blocker, not a silent omission. Rebuild output metadata without sharing mutable source objects. Full instrument/acquisition graphs, original Fileset/import metadata, ROIs, measurements, tags and attachments are outside v1. Reports state these exclusions.

Verify dimensions, dtype, channel order, calibration, required metadata and representative read-back pixels before completion. Test exact bypass equality with full small fixtures, not only rendered screenshots.

### 8.2 Provenance

Use a versioned project namespace for machine-readable annotations plus a human-readable description. Include source ID/name/group and parent dataset IDs; destination ID; run UUID and user/job identity; UTC timestamps; wrapper/worker/algorithm versions and hash; requested and resolved radii with zero-based indices; dimensions/dtype/calibration; fixed scientific settings; actual shrink factors including small-plane fallback; clipping/saturation statistics; output ID; completion state and fingerprint.

Weight clipping fractions by pixel count. Fingerprints include source ID, critical metadata, algorithm and canonical scientific settings. Source ID/metadata do not prove pixels have not changed; document the limitation and add content checksums if the deployment requires them. Names never establish identity or completion.

## 9. Failures, recovery and reports

### 9.1 Lifecycle and failure policy

```text
PLANNED -> VALIDATED -> PROCESSING -> PIXELS_WRITTEN
        -> METADATA_WRITTEN -> VERIFIED -> LINKED -> COMPLETE
```

Other reported outcomes include FILTERED_OUT, FAILED, CANCELLED and NOT_ATTEMPTED. All-zero copies follow the full completion lifecycle. Pixel write success alone is not completion.

Continue after isolated image/pixel errors, retaining incomplete output IDs and reasons. Stop on session expiry, systemic permission/storage/capacity failure or cancellation. Bound safe-read retries; never blindly retry image or dataset creation after uncertain writes. Reconcile server state first. Keep incomplete images unlinked where possible, marked and discoverable for operator recovery. Do not automatically delete originals, completed outputs, pre-existing destination content or this run's incomplete objects.

### 9.2 Durable journal and explicit resume

Before deployment, choose a durable operator-managed journal store outside temporary job directories, with a versioned schema, atomic record updates, retention and access controls. Record the input snapshot/configuration before output writes and record IDs/stages as soon as available. Use a real atomic lock/admission mechanism to prevent simultaneous resume. For multiple processor hosts, use shared durable storage/locking or documented routing back to the original host; a host-local file lock alone is insufficient.

`Resume_Run_ID` identifies an accessible run owned by the launching user in the active group. Other submitted settings must match its canonical configuration and original selection request; do not silently accept changed settings. Process the recorded input snapshot, not newly enumerated dataset children. Revalidate source metadata, permissions, destination and output state. Reuse the recorded new-dataset ID; fail clearly if the destination has been deleted rather than creating a replacement silently.

Skip only recorded COMPLETE outputs whose existence, metadata/state and links are verified. Reconcile incomplete/uncertain writes before deciding whether to finish or retry. Changed scientific settings/algorithm require a new run. Regular reruns create new UUIDs and outputs even when names match; cross-run reuse is outside v1.

### 9.3 Reports

Return a short Message, the resolved output dataset link/ID, and CSV/JSON reports. For large batches, return the dataset plus reports instead of hundreds of image links. New dataset IDs are reported only once actually assigned. If final reporting fails, preserve the durable journal for recovery.

Reports include run status/configuration; selected/matched/completed/failed/not-attempted counts; source/output IDs and names; dimensions/type/group/channel rules; destination; timings, resource estimates and measured values; warnings/errors and incomplete-object IDs. Keep progress bounded to image/plane counts, time and stage; no credentials or raw environment dumps. Escape report content for HTML and spreadsheet formula safety. Report attachment failure explicitly; it must not trigger duplicate image creation.

Preview lists planned names and destination request, with processing readiness. No matches returns a clear empty report. A completed image may remain durable even if later images or final reporting fail; show partial completion honestly.

## 10. Capacity, permissions and cancellation

Process one image and one working plane at a time per job. This does not guarantee one job across all users. Operator configuration must define admission/concurrency across the relevant processor hosts, per-job memory, maximum images/planes/XY pixels/radius, elapsed-time and worker-plane limits, temporary storage and repository capacity policy. If hard global admission is unavailable, document a restricted/manual rollout without claiming an enforced fleet-wide queue.

Estimate output bytes as `X*Y*Z*C*T*dtype_bytes`; a float64 working plane uses `X*Y*8` bytes, but several working arrays, padding, SciPy buffers, serialization and both processes coexist. Measure peak memory and runtime on representative/worst supported dimensions and radius thresholds, including small-plane shrink fallback. Include thumbnails/pyramids, journals/reports and failed intermediates in storage planning. Label unavailable quota/free-space checks honestly.

Verify raw-plane transfer/message limits before allowing large images. Reject inputs exceeding tested whole-plane limits. Do not independently tile rolling-ball computation: borders, shrink alignment and interpolation require a separately validated halo/grid design. Limit native threads through tested service configuration where appropriate; one Python process can start several native threads.

All OMERO operations use the launching user's rights and active group. Validate source reads, dataset creation/linking, optional project links and annotation permissions. Trusted installation does not bypass data permissions. The job needs OMERO API access and its own application/temp/journal paths, not direct database or binary-repository mutation.

Inventory installed script/session/process timeout semantics. Use a monotonic elapsed-time budget and stop admitting work when exhausted; check cancellation between planes. Test browser closure, cancellation, worker timeout, processor termination, OMERO restart and session expiry separately. Browser closure is not assumed to cancel a job. Ensure orphan worker cleanup and interrupted-output recovery do not depend solely on `finally`.

## 11. Implementation plan

1. **Start coding from this contract.** Read repository instructions and use the pinned source. Record unresolved deployment facts as installation prerequisites; do not block local implementation on server access or repeat requests for scientific validation. Do not infer server versions from documentation links.
2. **Package the pinned algorithm unchanged.** Build the isolated uv worker and preserve source hash/attribution. Accept the owner-confirmed RDI-calculator validation.
3. **Implement pure planning.** Validate ID cardinality, filters, exact channel coverage/layout, fixed naming, destination combinations, metadata and limits. Preview must be usable with inventory-only inputs.
4. **Prototype OMERO I/O.** Round-trip a tiny distinctive Z/C/T fixture in each supported dtype, metadata and explicit destination. Inject upload/generator failures and establish early ID tracking.
5. **Integrate worker and completion.** Stream bounded planes; copy zero channels exactly; create one output per distinct source; record provenance and verified links.
6. **Implement recovery.** Exercise uncertain dataset/image writes, durable journal updates, resume locks, cancellation and report failure.
7. **Verify the installed dialog.** Confirm multi-image/single-dataset selection, optional zero values, radius help, mutually exclusive destination fields, Preview and result links.
8. **Benchmark and stage.** Set measured capacity limits, run a non-admin test batch, and verify existing scripts remain operational without environment changes.
9. **Deliver and roll out.** Provide the implementation, integration results, runbooks and previous-release rollback. Production installation requires actual operational access and resolved deployment prerequisites; prior scientific validation is accepted.

### Integration check before deployment

The scientific validation matrix is omitted at the owner's request. Check the newly written OMERO integration with a small test image: selection, raw plane transfer and Z/C/T order, zero-radius copying, output naming, metadata and destination links. Prior RDI-calculator validation covers the algorithm; it does not establish that newly written OMERO I/O has been exercised. These integration checks happen when server access is available and do not delay starting the script.

## 12. Packaging, installation and documentation

Suggested separation:

```text
scripts/Batch_Rolling_Ball_Background.py     uploaded OMERO entry point
adapter/                                  planner, OMERO I/O, journal, reports
worker/src/omero_bgsub/                    pinned algorithm, worker protocol
worker/pyproject.toml
worker/uv.lock
tests/                                    focused planner and OMERO integration checks
docs/                                     installation, usage, recovery, rollback
```

The adapter must not import worker/SciPy dependencies. Deploy worker releases externally, not as a repository-local environment. Use the existing local Miniforge environment for OMERO CLI operations; no environment is provisioned by this spec edit.

Use the trusted [admin script workflow](https://omero.readthedocs.io/en/stable/developers/scripts/user-guide.html#script-writing-as-admin); verify syntax with the installed CLI before executing upload/list/params/launch/replace commands. Test both parameter discovery and execution under the service account. Record script ID/path/version and external adapter/worker paths. Do not edit bundled official scripts or assume the uploaded file brings its dependencies.

The user guide must explain input cardinality, metadata Preview, strict channel layouts, zero-based rules, zero-radius copying, existing-ID/new-name destinations, fixed naming and duplicate-name identification, rerun inclusion of previous outputs, partial completion/resume and the fact that `.tif` labels do not imply original TIFF files.

The operator runbook must record actual versions/paths, locked worker rebuilds, admission/limits, journal schema/location/retention, cancellation and incomplete-output recovery, trusted upload/replacement and parameter-cache checks, monitored logs, test dataset/expected results and rollback that preserves active jobs.

## 13. Reference index

Use version-matched documentation/source when implementing:

- [OMERO script user guide](https://omero.readthedocs.io/en/stable/developers/scripts/user-guide.html): trusted scripts, parameters, installation and outputs.
- [Script style/UI conventions](https://omero.readthedocs.io/en/stable/developers/scripts/style-guide.html) and [parameter API](https://omero-py.readthedocs.io/en/stable/omero.scripts.html).
- [OMERO Python API](https://omero.readthedocs.io/en/stable/developers/Python.html): compatibility, raw reads and creation examples.
- [Configuration glossary](https://omero.readthedocs.io/en/stable/sysadmins/config.html): launcher and timeout settings; verify deployed semantics.
- [Permissions](https://omero.readthedocs.io/en/stable/sysadmins/server-permissions.html), [container hierarchy](https://omero.readthedocs.io/en/stable/developers/json-api.html#projects-datasets-and-images) and [binary repository](https://omero.readthedocs.io/en/stable/sysadmins/unix/server-binary-repository.html).
- [Pinned algorithm](https://github.com/JeffLeeLab/Image-analysis/blob/e6360e0a7e04580ac30e143281edae66660f9ec6/people/AlfredoCastello/Zhiling-Marko-HIV/src/imagej_rolling_ball.py) and [ImageJ reference source](https://imagej.net/ij/developer/source/ij/plugin/filter/BackgroundSubtracter.java.html).
- [uv environments](https://docs.astral.sh/uv/pip/environments/) and [locking/syncing](https://docs.astral.sh/uv/concepts/projects/sync/).

## 14. Final instruction to the building agent

Implement this v1 contract, starting with the pure planner, isolated worker and small OMERO I/O prototype. Preserve originals, zero-radius channels, source dimensions and dtype. Require explicit destination selection and fixed output naming. Keep unresolved deployment facts visible and treat unknown API/client behaviour as a compatibility check requiring evidence. Start implementation now without waiting for server access or a new scientific validation exercise. Deliver code, locked environment, focused integration results and installation/recovery/rollback instructions. Record scientific validation as owner-confirmed from RDI-calculator; do not claim new independent validation or a deployment that has not occurred.
