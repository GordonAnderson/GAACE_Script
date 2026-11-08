// T2: a float goes out as a number, a float reply comes back.
// Sets the ADC filter time constant to 12.5 ms (a live setting, not saved
// unless you send SAVE) and reads it back; variable 0 should read 12.5.
// Note the original value first (GADCFILTER) and restore it afterwards.
cmd("SADCFILTER", 12.5);
filt = cmdf("GADCFILTER");
