#!/usr/bin/env python3

#
# Author: fmontorsi
# Created: June 2024
# License: Apache license
#


# =======================================================================================================
# CircularBuffer
# =======================================================================================================


class CircularBuffer:
    """
    This is a specialized implementation of a circular buffer designed to:
    * hold boolean/digital samples
    * hold non-uniformly-distributed samples: each sample has its companion timestamp and
      there is no fixed sampling frequency assumption
    * hold in the buffer only CHANGES in value: pushing twice the same value into the buffer
      (with different timestamps) means the second sample is merged with the first one
    * allow simple & efficient filtering for "stable" values discarding transient fluctuations
      if their duration is below a fixed threshold
    """

    def __init__(self, size: int):
        assert size > 0
        self.size = size
        # buffer is empty at the start
        # each entry is actually a tuple (TIMESTAMP;VALUE)
        self.buffer = [(None, None)] * size
        self.index = 0  # next writable location in the buffer
        self.last_timestamp = 0

    def push_sample(self, timestamp: int, value: bool) -> None:
        # timestamp handling
        if timestamp > self.last_timestamp:
            assert timestamp > 0
            self.last_timestamp = timestamp
        else:
            # fix invalid timestamp (NTP adjustment?)
            self.last_timestamp += 1
            timestamp = self.last_timestamp

        # check if this is a value transition
        if self.index > 0:
            last_index = (self.index - 1) % self.size
            if self.buffer[last_index][1] == value:
                # not a transition... just discard the new sample -- it brings no information actually
                # (to be fair: it provides the information that the value is STILL the same... but this
                #  is assumed implicitly to be the case when no new samples are present)
                return

        self.buffer[self.index % self.size] = (timestamp, value)
        self.index += 1

    def get_all_samples(self) -> list:
        if self.index == 0:
            return None  # buffer is empty
        if self.index <= self.size:
            return self.buffer[: self.index]  # return only valid/populated items
        idx_mod = self.index % self.size
        return self.buffer[idx_mod:] + self.buffer[:idx_mod]  # linearize the circular buffer

    def get_last_sample(self) -> tuple:
        return self.get_past_sample(1)  # look 1 sample in the past, which means the last pushed sample

    def get_past_sample(self, sample_offset_in_the_past: int) -> tuple:
        if self.index == 0:
            return None  # buffer is empty
        if self.index < sample_offset_in_the_past:
            # the caller is requesting a sample too much in the past -- beyond the buffer memory
            return None
        if sample_offset_in_the_past < 1:
            # sample_offset_in_the_past==0 (or negative) is not a sample in the past
            return None
        if sample_offset_in_the_past > self.size:
            # the caller is trying to explore too much in the past -- beyond the buffer memory
            return None
        last_index = (self.index - sample_offset_in_the_past) % self.size
        return self.buffer[last_index]

    def get_stable_sample(self, now_ts: int, min_stability_sec: float) -> tuple:
        if self.index == 0:
            return None  # buffer is empty
        if now_ts < self.last_timestamp:
            # fix invalid timestamp (NTP adjustment?)
            now_ts = self.last_timestamp
        # starting from the last sample search going backwards the first "stable" sample:
        last_ts = now_ts
        sample_offset_in_the_past = 1
        while sample_offset_in_the_past <= self.size:
            s = self.get_past_sample(sample_offset_in_the_past)
            if s is None:
                # trying to dig too much into the past... perhaps the circular buffer is not completely full yet...
                return None

            sample_age_sec = last_ts - s[0]
            if sample_age_sec >= min_stability_sec:
                # debug only:
                # print(
                #    f"sample_offset_in_the_past={sample_offset_in_the_past} -> sample_age_sec={sample_age_sec} -> STABLE for threshold {min_stability_sec}"
                # )
                # found a stable sample!
                return s

            # debug only:
            # print(
            #    f"sample_offset_in_the_past={sample_offset_in_the_past} -> sample_age_sec={sample_age_sec} -> UNSTABLE for threshold {min_stability_sec}"
            # )

            # keep going backward
            sample_offset_in_the_past += 1
            last_ts = s[0]

        # the whole buffer has been inspected but all value transitions were shorter than 'min_stability_sec'
        return None

    def clear(self) -> None:
        self.buffer = [(None, None)] * self.size
        self.index = 0
