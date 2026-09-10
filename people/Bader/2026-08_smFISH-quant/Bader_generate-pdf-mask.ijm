// ImageJ Macro: Batch 3D PDF segmentation of TIFF files in a directory
// Prompts for a directory and the segmentation settings, then for each TIFF:
// splits channels, rolling-ball background subtraction, median filter and
// auto threshold (all on the whole stack), saved as
// <dir>/PDFmask/PDFmask_<name>.tif
// A max intensity projection of each mask is also saved alongside it as
// <dir>/PDFmask/MIPqc_<name>.png for quick visual quality control.
//
// Masks go in their own subfolder so they are never picked up as input images
// by the bigfish batch script, whichever order the two are run in.

dir = getDirectory("Choose a directory with TIFF files");
if (dir == "") exit("No directory selected.");

methods = newArray("Default", "Huang", "Huang2", "Intermodes", "IsoData", "Li",
	"MaxEntropy", "Mean", "MinError(I)", "Minimum", "Moments", "Otsu",
	"Percentile", "RenyiEntropy", "Shanbhag", "Triangle", "Yen");

Dialog.create("Batch PDF segmentation");
Dialog.addNumber("Channel for PDF segmentation (1-based):", 1);
Dialog.addNumber("Rolling ball radius:", 50);
Dialog.addNumber("Median filter radius:", 2);
Dialog.addChoice("Auto threshold method:", methods, "Otsu");
Dialog.show();

pdf_channel = Dialog.getNumber();
rolling = Dialog.getNumber();
median_radius = Dialog.getNumber();
method = Dialog.getChoice();

mask_dir = dir + "PDFmask" + File.separator;
File.makeDirectory(mask_dir);
if (!File.exists(mask_dir)) exit("Could not create output directory: " + mask_dir);

setBatchMode(true);
list = getFileList(dir);
for (i = 0; i < list.length; i++) {
	if (!(endsWith(list[i], ".tif") || endsWith(list[i], ".tiff"))) continue;
	// masks now live in a subfolder, but skip any left beside the images
	// by an earlier version of this macro
	if (startsWith(list[i], "PDFmask_")) continue;

	open(dir + list[i]);
	origTitle = getTitle();
	base = File.nameWithoutExtension;		// capture before splitting

	run("Split Channels");
	selectWindow("C" + pdf_channel + "-" + origTitle);

	run("Subtract Background...", "rolling=" + rolling + " stack");
	run("Median...", "radius=" + median_radius + " stack");
	run("Auto Threshold", "method=" + method + " white stack use_stack_histogram");

	saveAs("Tiff", mask_dir + "PDFmask_" + base + ".tif");

	// max intensity projection of the mask, as a PNG for quick visual QC
	if (nSlices > 1) {
		run("Z Project...", "projection=[Max Intensity]");
	} else {
		run("Duplicate...", "title=MIP_" + base);	// single slice: nothing to project
	}
	saveAs("PNG", mask_dir + "MIPqc_" + base + ".png");

	close("*");					// mask + MIP + unused channels
}
setBatchMode(false);
print("Settings -- channel: " + pdf_channel + ", rolling: " + rolling + ", median: " + median_radius + ", threshold: " + method);
print("Done. Masks (PDFmask_*.tif) and QC projections (MIPqc_*.png) written to: " + mask_dir);
