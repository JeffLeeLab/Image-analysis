// split_subtract_merge.ijm
//
// Works on the currently open (active) multichannel image - not a batch macro.
//
// Order of operations:
//   1. Split the image into its channels.
//   2. One dialog asks, per channel:
//        - rolling ball radius for "Subtract Background" (0 = skip that channel)
//        - whether the channel goes into the merged composite
//        - which LUT the channel gets (default order: Blue, Green, Red, White, Magenta, Yellow, Cyan)
//   3. On OK: subtract background on every channel stack (all slices/frames).
//   4. Merge the selected channels into a composite and apply the chosen LUTs.

requires("1.52a");

if (nImages == 0)
    exit("No image is open.");

origTitle = getTitle();
getDimensions(imgW, imgH, nCh, nZ, nT);

if (nCh < 2)
    exit("The active image has only one channel - nothing to split.");

// LUT choices offered in the dialog, and the ImageJ command each one maps to
lutNames    = newArray("Blue",  "Green", "Red", "White", "Magenta", "Yellow", "Cyan", "Grays", "Fire", "Ice");
lutCommands = newArray("Blue",  "Green", "Red", "Grays", "Magenta", "Yellow", "Cyan", "Grays", "Fire", "Ice");
defaultOrder = newArray("Blue", "Green", "Red", "White", "Magenta", "Yellow", "Cyan");

// ---------------------------------------------------------------- 1. split
run("Split Channels");

chTitles = newArray(nCh);
for (c = 1; c <= nCh; c++) {
    chTitles[c-1] = "C" + c + "-" + origTitle;
    if (!isOpen(chTitles[c-1]))
        exit("Expected split channel window not found: " + chTitles[c-1]);
}

// -------------------------------------------------------------- 2. dialog
Dialog.create("Background subtraction and merge");
Dialog.addMessage("Image: " + origTitle + "   (" + nCh + " channels, " + nZ + " slices, " + nT + " frames)");
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
Dialog.addString("Merged image name", "Merged-" + origTitle, 30);
Dialog.addCheckbox("Close channels that are not merged", false);
Dialog.show();

radii   = newArray(nCh);
merge   = newArray(nCh);
lutCmd  = newArray(nCh);
for (c = 1; c <= nCh; c++) {
    radii[c-1] = Dialog.getNumber();
    merge[c-1] = Dialog.getCheckbox();
    chosen     = Dialog.getChoice();
    lutCmd[c-1] = "Grays";
    for (k = 0; k < lutNames.length; k++)
        if (lutNames[k] == chosen) lutCmd[c-1] = lutCommands[k];
}
mergedName   = Dialog.getString();
closeUnmerged = Dialog.getCheckbox();

// ----------------------------------------------- 3. background subtraction
setBatchMode(true);
for (c = 1; c <= nCh; c++) {
    if (radii[c-1] > 0) {
        selectWindow(chTitles[c-1]);
        opts = "rolling=" + radii[c-1];
        if (nZ * nT > 1) opts = opts + " stack";
        run("Subtract Background...", opts);
    }
}

// -------------------------------------------------------- 4. merge + LUTs
nMerge = 0;
mergeArgs = "";
mergeLuts = newArray(nCh);
for (c = 1; c <= nCh; c++) {
    if (merge[c-1]) {
        nMerge++;
        mergeArgs = mergeArgs + "c" + nMerge + "=[" + chTitles[c-1] + "] ";
        mergeLuts[nMerge-1] = lutCmd[c-1];
    }
}

if (nMerge == 0) {
    setBatchMode("exit and display");
    exit("No channels selected for merging. Background-subtracted channels are left open.");
}

if (nMerge == 1) {
    // Merge Channels needs at least two images; just recolour and rename the single one
    for (c = 1; c <= nCh; c++)
        if (merge[c-1]) selectWindow(chTitles[c-1]);
    run(mergeLuts[0]);
    rename(mergedName);
} else {
    run("Merge Channels...", mergeArgs + "create");
    rename(mergedName);
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

if (closeUnmerged) {
    for (c = 1; c <= nCh; c++)
        if (!merge[c-1] && isOpen(chTitles[c-1])) {
            selectWindow(chTitles[c-1]);
            close();
        }
}

setBatchMode("exit and display");
selectWindow(mergedName);
