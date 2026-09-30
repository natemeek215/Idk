# Etude 4: Three Helm Synth Patches

Prompts chosen: **bell-like**, **grows over time**, **percussive**

| # | Patch | Prompt | Files |
|---|-------|--------|-------|
| 1 | Glass Bell | bell-like | `Meek_etude4_GlassBell.*` |
| 2 | Slow Bloom | grows over time | `Meek_etude4_SlowBloom.*` |
| 3 | Wood Knock | percussive | `Meek_etude4_WoodKnock.*` |

- `Meek_etude4_ThreeHelmSynths.mp4` is the demo video. It shows the Helm interface while each patch plays, and a caption lists the parameters I changed.
- `*_interface.png` is a screenshot of each patch in Helm.
- `*_modulation.png` shows each patch with the Mod Envelope's modulation view open. The outlined controls are the ones the envelope modulates.
- `*.wav` is a separate audio recording of each demo.
- `*.helm` is the patch file. Open it with Helm's BROWSE button, or drag it onto Helm.

## Patch settings

### 1. Glass Bell (bell-like)
- **Oscillators:** two sine waves. Osc 2 is transposed up 2 octaves and detuned +41 cents, which makes its overtone slightly "wrong" (inharmonic), the way a real bell's overtones are.
- **FM:** the oscillator MOD knob (cross-modulation) is at 0.12. The Mod Envelope pushes it up 0.3 for about 0.5 s on each note, so the strike sounds bright and metallic and then settles into a purer tone.
- **Amp envelope:** almost no attack, a 3.5 s decay, 0 sustain and a 2.5 s release, so the note rings out and fades like a struck bell.
- **Movement:** Mono LFO 1 runs at 5.5 Hz and slightly wobbles the tuning of osc 2, which gives a shimmer.
- **Space:** a dotted-1/8 delay and a large reverb (feedback 0.95). In the video I turn the reverb MIX up while the chord rings.

### 2. Slow Bloom (grows over time)
- **Oscillators:** osc 1 is a saw with 5 unison voices. A sub oscillator (square wave, one octave down) adds low end. Osc 2 is a saw a fifth higher, but it starts at volume 0.
- **Amp envelope:** a 4 s attack.
- **Filter:** a 24 dB low-pass that starts nearly closed (cutoff 42). A filter envelope with a 7 s attack and +58 semitones of depth opens it slowly.
- **Mod Envelope (10 s attack):**
  - fades osc 2 in, so the fifth appears
  - widens the unison detune from 12 to 52 cents
  - speeds Mono LFO 2 up from 0.4 Hz to about 4 Hz (LFO 2 moves the filter cutoff)
  - raises the distortion drive
- **Result:** the longer you hold a chord, the brighter, wider, more harmonically full and more restless it gets.

### 3. Wood Knock (percussive)
- **Sources:** a triangle wave, plus a sine 19 semitones up, plus some noise.
- **Amp envelope:** 0 attack, a 0.2 s decay and 0 sustain, so there is no held note.
- **Mod Envelope (50 ms):** pitches both oscillators down one octave over the attack and adds a burst of noise. That quick "thwack" is what makes it sound hit instead of played.
- **Filter:** a 24 dB low-pass with key tracking and a very short filter envelope (0.1 s decay), which gives a snappy tone.
- **Extras:** soft-clip distortion and a small, dark reverb.
- **In the video:** I sweep the filter ENV DEPTH knob up while the pattern plays, and the knock goes from woody to bright and clicky.

## Questions

> **Note:** these answers are a draft. Edit them so they describe what *you* heard and did.

**What did you experiment with in Helm?**
Mostly envelopes and modulation. I found that the Mod Envelope is the most powerful tool in Helm. Aimed at different targets, the same envelope shape made a bell's metallic strike (FM amount), a drum's pitch drop (oscillator transpose), and a pad that evolves (unison detune, osc 2 volume, LFO speed and distortion). I also tried:
- detuning an oscillator by an odd number of cents to get inharmonic, bell-like overtones
- the 12 dB vs. 24 dB filter slopes
- key tracking
- mixing in the noise oscillator
- the delay and reverb sections

**What challenges did you face?**
- **Bell:** getting it to sound like a bell and not an electric piano. With plain harmonic intervals (octave, fifth), it sounded like an electric piano. It only sounded like a bell once osc 2 was detuned by a "wrong" amount and the FM was limited to the attack.
- **Percussive patch:** getting it to sound hit. At first it just sounded like a short beep. A very fast pitch drop and a noise click in the first 50 ms fixed that.
- **Slow Bloom:** balancing the growth. If everything opens at the same rate, it sounds like one long fade-in. It sounds like it's growing because the filter, the added fifth and the LFO speed change on different timescales (4 s, 7 s and 10 s).
- **Recording:** stacking several chord notes on a bright patch clipped the output, so I had to lower the master volume.

**What inspired each sound?**
- **Glass Bell:** wind chimes and glass/celesta bells. Their sound is bright and clangy at the moment of the strike, and then a long, pure tone rings on.
- **Slow Bloom:** a flower opening, or a time-lapse of a sunrise. It starts dark and small, then gradually gets brighter, wider and more alive the longer you hold it.
- **Wood Knock:** hand percussion like woodblocks, a log drum and the low toms of an 80s drum machine. I played it as a little rhythmic riff to show it works as a drum part and not just as a note.
