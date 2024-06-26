import pytest
import time
from raspy2mqtt.circular_buffer import CircularBuffer


@pytest.mark.unit
def test_circbuf_push_sample():
    circular_buffer = CircularBuffer(3)
    circular_buffer.push_sample(int(time.time()), False)
    circular_buffer.push_sample(int(time.time()), True)
    circular_buffer.push_sample(int(time.time()), False)
    samples = circular_buffer.get_all_samples()
    assert len(samples) == 3
    assert samples[0][1] == False
    assert samples[1][1] == True
    assert samples[2][1] == False


@pytest.mark.unit
def test_circbuf_push_sample_wrong_ts():
    circular_buffer = CircularBuffer(3)
    circular_buffer.push_sample(100, False)
    circular_buffer.push_sample(99, True)
    circular_buffer.push_sample(98, False)
    samples = circular_buffer.get_all_samples()
    assert len(samples) == 3
    assert samples[0] == (100, False)
    assert samples[1] == (101, True)  # timestamp has been fixed
    assert samples[2] == (102, False)  # timestamp has been fixed


@pytest.mark.unit
def test_circbuf_circular_behavior():
    circular_buffer = CircularBuffer(3)
    circular_buffer.push_sample(int(time.time()), False)
    circular_buffer.push_sample(int(time.time()), True)
    circular_buffer.push_sample(int(time.time()), False)
    circular_buffer.push_sample(int(time.time()), True)
    circular_buffer.push_sample(int(time.time()), False)
    samples = circular_buffer.get_all_samples()
    assert len(samples) == 3
    assert samples[0][1] == False
    assert samples[1][1] == True
    assert samples[2][1] == False


@pytest.mark.unit
def test_circbuf_push_sample_merge_behavior():
    circular_buffer = CircularBuffer(3)
    circular_buffer.push_sample(100, False)
    circular_buffer.push_sample(101, False)
    circular_buffer.push_sample(102, False)
    circular_buffer.push_sample(103, False)
    circular_buffer.push_sample(104, True)
    # many samples but just 1 transition, so we expect 2 samples into the buffer:
    samples = circular_buffer.get_all_samples()
    assert len(samples) == 2
    assert samples[0] == (100, False)
    assert samples[1] == (104, True)
    # add 1 more transition, so we can now expect 3 samples into the buffer:
    circular_buffer.push_sample(105, False)
    samples = circular_buffer.get_all_samples()
    assert len(samples) == 3
    assert samples[0] == (100, False)
    assert samples[1] == (104, True)
    assert samples[2] == (105, False)


@pytest.mark.unit
def test_circbuf_get_last_sample():
    circular_buffer = CircularBuffer(3)
    assert circular_buffer.get_last_sample() == None
    circular_buffer.push_sample(1, False)
    circular_buffer.push_sample(2, True)
    assert circular_buffer.get_last_sample() == (2, True)
    circular_buffer.push_sample(3, False)
    circular_buffer.push_sample(4, False)
    circular_buffer.push_sample(5, True)
    assert circular_buffer.get_last_sample() == (5, True)
    circular_buffer.push_sample(6, False)
    assert circular_buffer.get_last_sample() == (6, False)


@pytest.mark.unit
def test_circbuf_get_past_sample():
    circular_buffer = CircularBuffer(3)
    assert circular_buffer.get_past_sample(1) == None
    circular_buffer.push_sample(10, False)
    circular_buffer.push_sample(20, True)
    circular_buffer.push_sample(30, False)
    circular_buffer.push_sample(40, False)  # this sample gets merged with previous one
    circular_buffer.push_sample(50, True)
    # the internal buffer status is expected to be:
    #
    #   index|timestamp|value
    #   0    |50       |True          <-- this entry was initially (10, False) but then gets overwritten
    #   1    |20       |True
    #   2    |30       |False         <-- this entry has absorbed/was-merged with the entry (40, False)
    #
    #   circular_buffer.index = next writable entry = 1
    #
    # note that the first sample (1,False) has been "forgot" -- too old compared to the buffer memory of 3 transitions
    assert circular_buffer.get_past_sample(1) == (50, True)
    assert circular_buffer.get_past_sample(2) == (30, False)
    assert circular_buffer.get_past_sample(3) == (20, True)
    assert circular_buffer.get_past_sample(4) == None


@pytest.mark.unit
def test_circbuf_get_stable_sample1():
    circular_buffer = CircularBuffer(3)
    # lots of value transitions in a narrow time window [10s-50s] and then a final state transition after +40sec
    circular_buffer.push_sample(10, False)
    circular_buffer.push_sample(20, True)
    circular_buffer.push_sample(30, False)
    circular_buffer.push_sample(40, False)  # this sample gets merged with previous one
    circular_buffer.push_sample(50, True)
    circular_buffer.push_sample(60, True)  # this sample gets merged with previous one
    circular_buffer.push_sample(90, False)

    now_ts = 90 + 5
    # if we ask at +5sec since last sample, a sample stable for at least 1sec, we'll get the last sample
    assert circular_buffer.get_stable_sample(now_ts, min_stability_sec=1) == (90, False)
    # if we ask at +5sec since last sample, a sample stable for at least 10sec, we'll get the penultimate sample
    assert circular_buffer.get_stable_sample(now_ts, min_stability_sec=10) == (50, True)
    # if we ask at +5sec since last sample, a sample stable for at least 50sec, we will find none
    assert circular_buffer.get_stable_sample(now_ts, min_stability_sec=50) == None

    # if we ask at +15sec since last sample, a sample stable for at least 14.5sec, we'll get the last asmple
    now_ts = 90 + 15
    assert circular_buffer.get_stable_sample(now_ts, min_stability_sec=14.5) == (90, False)
    # if we ask at +15sec since last sample, a sample stable for at least 15.1sec, we'll get the penultimate sample
    assert circular_buffer.get_stable_sample(now_ts, min_stability_sec=15.1) == (50, True)


@pytest.mark.unit
def test_circbuf_get_stable_sample2():
    circular_buffer = CircularBuffer(10)
    # a stable value for a lot of time, then a lot of quick transitions in the time window [50s-60s]
    circular_buffer.push_sample(1, False)
    circular_buffer.push_sample(50, True)
    circular_buffer.push_sample(51, False)
    circular_buffer.push_sample(52, True)
    circular_buffer.push_sample(53, False)
    circular_buffer.push_sample(56, True)
    circular_buffer.push_sample(60, False)

    now_ts = 60 + 1
    # if we ask at +1sec since last sample, a sample stable for at least 2sec, we'll get the penultimate sample
    assert circular_buffer.get_stable_sample(now_ts, min_stability_sec=2) == (56, True)
    # if we ask at +1sec since last sample, a sample stable for at least 5sec, we'll get back to the very first sample...
    assert circular_buffer.get_stable_sample(now_ts, min_stability_sec=5) == (1, False)


@pytest.mark.unit
def test_circbuf_clear():
    circular_buffer = CircularBuffer(3)
    circular_buffer.push_sample(int(time.time()), False)
    circular_buffer.push_sample(int(time.time()), True)
    circular_buffer.clear()
    assert circular_buffer.get_all_samples() == None
    assert circular_buffer.get_last_sample() == None
