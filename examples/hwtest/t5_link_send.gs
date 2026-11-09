// T5: link_ready() and scale_send() against a downstream instrument.
// The target is the repeater's ADC target (SADCDEV) and the command its
// SADCCMD prefix; scale_send(counts) sends "<cmd>,<ADCM*counts+ADCB>".
// Variables: linked = link_ready() (1 = target connected and idle),
// counts = raw ADC reading, sent = scale_send() result (1 = queued, 0 = not).
linked = link_ready();
counts = read_adc();
sent = 0;
if (linked) {
    sent = scale_send(counts);
}
