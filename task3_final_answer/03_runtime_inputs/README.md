# Runtime inputs

The dataset is not in git. Point `T3_DATA` at any directory that contains the
two files below; `config.py` searches it recursively.

```
Training_set.csv    1,285 labelled flows from 400 source calls
Testing_set.csv     327 flows from 100 further held-out calls
```

Each row is one UDP media flow, described by its first five packets:

```
relative_time_0 … relative_time_4     arrival time relative to the first packet
packet_length_0 … packet_length_4     UDP payload length in bytes
label                                 training set only; one of ten classes
```

Calls are balanced across the ten classes; flows are not (40 to 256 per class),
because a call yields a variable number of flows. The data carries no call
identifier.
