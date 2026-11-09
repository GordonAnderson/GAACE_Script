// Full system test: read the repeater's ADC, convert to volts with the
// repeater's own calibration (volts = M * counts + B, from GADCM/GADCB),
// map 0..10 V to 0..100% drive, and send it to RFgen as SDRV,<drv>.
// Inputs below 0 V clamp to 0%, above 10 V to 100%.
// Variables: counts = raw ADC reading (0..4095), volts = input voltage,
// drv = drive % sent, ok = 1 if RFgen ACKed, 0 if it was not reachable or
// the command was NAKed.
counts = read_adc();
volts = cmdf("GADCM") * counts + cmdf("GADCB");
drv = volts * 10.0;           // 10% per volt
if (drv < 0.0) { drv = 0.0; }
if (drv > 100.0) { drv = 100.0; }
rf = device("RFgen");
ok = 0;
if (ready(rf)) {
    ok = dcmd(rf, "SDRV", drv);
}
