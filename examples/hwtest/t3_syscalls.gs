// T3: syscalls from the device's table (read-only ones). Variable 0 is the
// raw ADC reading, variable 1 the DIO line level (0 or 1).
counts = read_adc();
din = dio_read();
