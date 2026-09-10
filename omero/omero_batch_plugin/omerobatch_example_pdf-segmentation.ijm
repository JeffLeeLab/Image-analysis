origTitle = getTitle();    // store name of original stack
run("Split Channels");

// Close others
selectWindow("C1-" + origTitle);
close();
selectWindow("C2-" + origTitle);
close();
selectWindow("C4-" + origTitle);
close();

selectWindow("C3-" + origTitle);

run("Subtract Background...", "rolling=50 stack");
run("Median...", "radius=2 stack");
run("Auto Threshold", "method=Otsu white stack use_stack_histogram");