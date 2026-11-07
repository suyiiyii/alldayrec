// The microphone as the server's voice socket (/api/ask/voice/ws) takes it: 16 kHz mono int16, a block every 0.1 s.
export const RATE = 16000,
  BLOCK = RATE / 10;

// Float samples at `rate` (an AudioContext's: 48 kHz on iPhones, 44.1 or 48 kHz elsewhere) to 16 kHz int16, call after
// call: each output sample is the mean of the input samples it spans (a box filter, enough against aliasing for speech
// recognition). ponytail: rates under 16 kHz come out short; no device's AudioContext runs that low.
export function resampler(rate: number) {
  const step = rate / RATE;
  let next = step, // where the output sample being summed ends, in input samples from this call's first
    sum = 0,
    n = 0;
  return (x: Float32Array): Int16Array<ArrayBuffer> => {
    const out: number[] = [];
    for (let i = 0; i < x.length; i++) {
      sum += x[i];
      n++;
      if (i + 1 >= next) {
        out.push(Math.round(Math.max(-1, Math.min(1, sum / n)) * 32767));
        sum = n = 0;
        next += step;
      }
    }
    next -= x.length;
    return Int16Array.from(out);
  };
}

// Samples into blocks of BLOCK for send; flush() sends what is left, a shorter block.
export function blocks(send: (b: Int16Array<ArrayBuffer>) => void) {
  let buf = new Int16Array(BLOCK),
    n = 0;
  return {
    push(x: Int16Array) {
      for (let i = 0; i < x.length;) {
        const k = Math.min(BLOCK - n, x.length - i);
        buf.set(x.subarray(i, i + k), n);
        n += k;
        i += k;
        if (n === BLOCK) {
          send(buf);
          buf = new Int16Array(BLOCK);
          n = 0;
        }
      }
    },
    flush() {
      if (n) send(buf.slice(0, n));
      n = 0;
    },
  };
}
