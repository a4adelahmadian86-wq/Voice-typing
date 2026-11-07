import struct
import unittest

from app.audio import estimate_noise_floor, normalize_speech_pcm, pcm_frame_rms, speech_threshold, trim_speech_frames


class AudioHelpersTests(unittest.TestCase):
    def test_pcm_frame_rms_uses_signed_little_endian_samples(self):
        frame = struct.pack("<320h", *([1000] * 320))
        self.assertAlmostEqual(pcm_frame_rms(frame), 1000.0)

    def test_pcm_frame_rms_rejects_incomplete_frames(self):
        self.assertEqual(pcm_frame_rms(b"\x00" * 20), 0.0)

    def test_noise_floor_uses_quieter_half_to_reject_transient_peaks(self):
        self.assertAlmostEqual(estimate_noise_floor([40, 42, 39, 41, 1500, 1800]), 40.0)

    def test_speech_threshold_is_conservative_and_tracks_noise(self):
        self.assertEqual(speech_threshold(25), 140.0)
        self.assertEqual(speech_threshold(65), 143.0)
        self.assertAlmostEqual(speech_threshold(100), 220.0)

    def test_normalization_lifts_quiet_speech_without_clipping(self):
        pcm = struct.pack("<640h", *([500] * 640))
        normalized, gain, original_rms = normalize_speech_pcm(pcm)
        output = struct.unpack("<640h", normalized)
        self.assertGreater(gain, 1.0)
        self.assertLessEqual(max(abs(value) for value in output), 30000)
        self.assertGreater(original_rms, 0.0)
        self.assertGreater(sum(value * value for value in output), sum(value * value for value in struct.unpack("<640h", pcm)))

    def test_normalization_leaves_loud_audio_unchanged(self):
        pcm = struct.pack("<640h", *([10000] * 640))
        normalized, gain, _ = normalize_speech_pcm(pcm)
        self.assertEqual(normalized, pcm)
        self.assertEqual(gain, 1.0)

    def test_normalization_leaves_silence_unchanged(self):
        pcm = b"\x00" * 1280
        normalized, gain, speech_rms = normalize_speech_pcm(pcm)
        self.assertEqual(normalized, pcm)
        self.assertEqual(gain, 1.0)
        self.assertEqual(speech_rms, 0.0)

    def test_trim_speech_frames_removes_long_silence_but_keeps_short_margins(self):
        silence = struct.pack("<320h", *([0] * 320))
        speech = struct.pack("<320h", *([3000] * 320))
        frames = [silence] * 10 + [speech] * 20 + [silence] * 30
        trimmed = trim_speech_frames(frames, threshold=1000, pre_roll_frames=2, post_roll_frames=3)
        self.assertEqual(len(trimmed), 25)
        self.assertEqual(trimmed[:2], [silence, silence])
        self.assertEqual(trimmed[-3:], [silence, silence, silence])
        self.assertTrue(all(frame == speech for frame in trimmed[2:-3]))

    def test_trim_speech_frames_leaves_all_quiet_input_unchanged(self):
        silence = struct.pack("<320h", *([0] * 320))
        frames = [silence] * 12
        self.assertEqual(trim_speech_frames(frames, threshold=1000), frames)


if __name__ == "__main__":
    unittest.main()
