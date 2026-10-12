# Running background subtraction

1. In OMERO.web select one/multiple ordinary images, or one dataset. Open **Batch_Rolling_Ball_Background** from Scripts. If selection did not populate, enter the appropriate `Data_Type` and `IDs` manually. Do not mix types or pass multiple dataset IDs.
2. Leave `Run_Mode=Preview`. With no rules, this returns inventory and marks processing readiness incomplete. Download the JSON/CSV reports to inspect names, zero-based indices and dimensions.
3. Choose `Channel_Match_Mode=Name` for exact stored names or `Index` for `0, 1, 2, ...`. Enter a rule and explicit radius for **every** channel. Unused rows must have both fields absent/unchecked. A missing radius is different from a radius of zero.
4. **Set radius to 0 to skip background subtraction for that channel. The channel is kept unchanged in the output image. If all radii are 0, a copy of the image is created.** Positive values are radii in XY pixels.
5. Choose the destination:
   - `Existing_Dataset`: supply `Output_Dataset_ID`; leave new-name/project fields absent.
   - `New_Dataset`: supply `New_Dataset_Name`; leave dataset ID absent. An optional `Output_Project_ID` links the new dataset to that project. Otherwise it is a dataset without a project link. OMERO assigns its ID during Process.
6. Preview again. Fix all errors before Process. Every image must have the same channel count and ordered names; X/Y/Z/T dimensions may vary. Blank/duplicate channel names require Index mode, where you are responsible for consistent biological acquisition order.
7. Reopen the form and use the same values with `Run_Mode=Process`. Do not assume your client retained earlier settings. The output includes a dataset ID/link, run ID and JSON/CSV reports.

Example: source `sample.ome.tif` with channels DAPI/GFP/Cy5 and radii 0/50/100 produces `sample_bgs-0-50-100.tif`. Rule-row order does not change the suffix order: radii always follow source channel indices. There is no suffix/template field.

Settings are fixed: presmoothing on, automatic shrink, dark background/bright foreground, negative corrected values clipped, original uint8/uint16/float32 type preserved. Original images are retained. Pixels with NaN/Inf are rejected, including in bypass channels.

An output is a regular OMERO image. Its `.tif` name does not mean an original TIFF file has been exported. Duplicate output names are allowed; use image IDs, source IDs and run provenance to distinguish them. Repeating a normal run creates new outputs. Dataset inputs include earlier derived images if they match the filter; Preview the frozen input list before running, especially when input/output datasets coincide.

If one image has bad pixels, other images may finish and the report will show partial failure. Session/storage/worker-capacity errors stop the batch. Do not launch a fresh run to recover partially completed work: supply the previous `Resume_Run_ID` and the exact original parameters, including the original new-dataset name if used. Resume reuses the saved destination and input IDs. The same OMERO user and active group must be used. Ask the administrator about an uncertain-creation error; the script deliberately refuses to guess that no write happened.

The first release admits one job at a time per configured lock directory. A busy message means retry later. Closing the browser does not establish that the script stopped. Use OMERO's job controls and consult the administrator if cancellation is unclear.
