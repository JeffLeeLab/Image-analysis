// ImageJ Macro: Batch 3D PDF segmentation of TIFF files in a directory
// Prompts for a directory and the channel to segment, then for each TIFF:
// splits channels, rolling-ball background subtraction, median filter and
// Otsu auto threshold (all on the whole stack), saved as PDFmask_<name>.tif

dir = getDirectory("Choose a directory with TIFF files");
if (dir == null) exit("No directory selected.");

pdf_channel = getNumber("Channel for PDF segmentation (1-based):", 1);

setBatchMode(true);
list = getFileList(dir);
for (i = 0; i < list.length; i++) {
	if (!(endsWith(list[i], ".tif") || endsWith(list[i], ".tiff"))) continue;
	if (startsWith(list[i], "PDFmask_")) continue;	// don't reprocess outputs

	open(dir + list[i]);
	origTitle = getTitle();
	base = File.nameWithoutExtension;		// capture before splitting

	run("Split Channels");
	selectWindow("C" + pdf_channel + "-" + origTitle);

	run("Subtract Background...", "rolling=50 stack");
	run("Median...", "radius=2 stack");
	run("Auto Threshold", "method=Otsu white stack use_stack_histogram");

	saveAs("Tiff", dir + "PDFmask_" + base + ".tif");
	close("*");					// mask + unused channels
}
setBatchMode(false);
print("Done.");
