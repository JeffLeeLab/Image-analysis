// batch_split_subtract_merge.ijm
//
// Batch version: processes every image in an input folder and saves the
// merged composite to an output folder as TIFF.
//
// Steps per image:
//   1. Open the image and split its channels.
//   2. Subtract background (rolling ball, whole stack) per channel; radius 0 = skip.
//   3. Merge the selected channels into a composite and apply the chosen LUTs
//      (default order: Blue, Green, Red, White, Magenta, Yellow, Cyan).
//   4. Save as <name>_merged.tif in the output folder and close everything.
//
// The per-channel settings are asked ONCE, using the channel count of the
// first matching image in the input folder. Images with a different channel
// count are skipped and reported in the Log window.

requires("1.52a");

// ---------------------------------------------------------- folders + filter
inputDir  = getDirectory("Choose the INPUT folder");
outputDir = getDirectory("Choose the OUTPUT folder");

Dialog.create("Batch: file filter");
Dialog.addString("Process files ending with", ".tif", 10);
Dialog.addMessage("Example: .tif  .tiff  .nd2  .czi  (Bio-Formats is used for non-TIFF files)");
Dialog.show();
ext = toLowerCase(Dialog.getString());

// collect matching files
allFiles = getFileList(inputDir);
files = newArray(0);
for (f = 0; f < allFiles.length; f++) {
    name = allFiles[f];
    if (!endsWith(name, "/") && endsWith(toLowerCase(name), ext))
        files = Array.concat(files, name);
}
if (files.length == 0)
    exit("No files ending with '" + ext + "' found in " + inputDir);
files = Array.sort(files);

// ------------------------------------------ open first image to get channels
setBatchMode(true);
openImage(inputDir + files[0]);
getDimensions(imgW, imgH, nCh, nZ, nT);
firstTitle = getTitle();
close("*");
setBatchMode(false);

if (nCh < 2)
    exit("The first image (" + firstTitle + ") has only one channel - nothing to split.");

// LUT choices offered in the dialog, and the ImageJ command each one maps to
lutNames     = newArray("Blue", "Green", "Red", "White", "Magenta", "Yellow", "Cyan", "Grays", "Fire", "Ice");
lutCommands  = newArray("Blue", "Green", "Red", "Grays", "Magenta", "Yellow", "Cyan", "Grays", "Fire", "Ice");
defaultOrder = newArray("Blue", "Green", "Red", "White", "Magenta", "Yellow", "Cyan");

// ------------------------------------------------------------ settings dialog
Dialog.create("Batch background subtraction and merge");
Dialog.addMessage(files.length + " file(s) to process. Settings based on first image: " + firstTitle);
Dialog.addMessage(nCh + " channels, " + nZ + " slices, " + nT + " frames");
Dialog.addMessage("Rolling ball radius 0 = no background subtraction for that channel.");
for (c = 1; c <= nCh; c++) {
    if (c <= defaultOrder.length) defLut = defaultOrder[c-1];
    else                          defLut = "Grays";
    Dialog.addMessage("--- Channel " + c + " ---");
    Dialog.addNumber("C" + c + " rolling ball radius", 50, 0, 6, "px");
    Dialog.addCheckbox("C" + c + " include in merge", true);
    Dialog.addToSameRow();
    Dialog.addChoice("C" + c + " LUT", lutNames, defLut);
}
Dialog.addMessage(" ");
Dialog.addString("Output file suffix", "_merged", 15);
Dialog.show();

radii  = newArray(nCh);
merge  = newArray(nCh);
lutCmd = newArray(nCh);
for (c = 1; c <= nCh; c++) {
    radii[c-1] = Dialog.getNumber();
    merge[c-1] = Dialog.getCheckbox();
    chosen     = Dialog.getChoice();
    lutCmd[c-1] = "Grays";
    for (k = 0; k < lutNames.length; k++)
        if (lutNames[k] == chosen) lutCmd[c-1] = lutCommands[k];
}
suffix = Dialog.getString();

nMerge = 0;
for (c = 1; c <= nCh; c++)
    if (merge[c-1]) nMerge++;
if (nMerge == 0)
    exit("No channels selected for merging.");

// ------------------------------------------------------------------- run
print("\\Clear");
print("Batch split / subtract background / merge");
print("Input:  " + inputDir);
print("Output: " + outputDir);

setBatchMode(true);
nDone = 0;
nSkipped = 0;
for (f = 0; f < files.length; f++) {
    fileName = files[f];
    showProgress(f, files.length);
    showStatus("Processing " + (f+1) + "/" + files.length + ": " + fileName);

    openImage(inputDir + fileName);
    title = getTitle();
    getDimensions(w, h, ch, z, t);

    if (ch != nCh) {
        print("SKIPPED (" + ch + " channels, expected " + nCh + "): " + fileName);
        close("*");
        nSkipped++;
        continue;
    }

    // 1. split
    run("Split Channels");
    chTitles = newArray(nCh);
    for (c = 1; c <= nCh; c++)
        chTitles[c-1] = "C" + c + "-" + title;

    // 2. background subtraction
    for (c = 1; c <= nCh; c++) {
        if (radii[c-1] > 0) {
            selectWindow(chTitles[c-1]);
            opts = "rolling=" + radii[c-1];
            if (z * t > 1) opts = opts + " stack";
            run("Subtract Background...", opts);
        }
    }

    // 3. merge + LUTs
    mergeArgs = "";
    mergeLuts = newArray(nMerge);
    m = 0;
    for (c = 1; c <= nCh; c++) {
        if (merge[c-1]) {
            m++;
            mergeArgs = mergeArgs + "c" + m + "=[" + chTitles[c-1] + "] ";
            mergeLuts[m-1] = lutCmd[c-1];
        }
    }

    if (nMerge == 1) {
        for (c = 1; c <= nCh; c++)
            if (merge[c-1]) selectWindow(chTitles[c-1]);
        run(mergeLuts[0]);
    } else {
        run("Merge Channels...", mergeArgs + "create");
        if (!is("composite"))
            run("Make Composite", "display=Composite");
        for (i = 1; i <= nMerge; i++) {
            Stack.setChannel(i);
            run(mergeLuts[i-1]);
        }
        Stack.setChannel(1);
        if (is("composite"))
            Stack.setDisplayMode("composite");
    }

    // 4. save
    baseName = stripExtension(fileName);
    outPath = outputDir + baseName + suffix + ".tif";
    saveAs("Tiff", outPath);
    print("Saved: " + outPath);
    nDone++;

    close("*");
}
setBatchMode(false);

showProgress(1);
print("Done. " + nDone + " processed, " + nSkipped + " skipped.");

// ---------------------------------------------------------------- helpers
function openImage(path) {
    lower = toLowerCase(path);
    if (endsWith(lower, ".tif") || endsWith(lower, ".tiff"))
        open(path);
    else
        run("Bio-Formats Importer", "open=[" + path + "] color_mode=Default view=Hyperstack stack_order=XYCZT");
}

function stripExtension(name) {
    dot = lastIndexOf(name, ".");
    if (dot > 0) return substring(name, 0, dot);
    return name;
}
