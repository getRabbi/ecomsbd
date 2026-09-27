import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../core/env.dart';
import '../../design/tokens.dart';

/// The `ecomsbd` wordmark in motion, for brand moments such as the splash.
///
/// The letters rise into place one after another with a slight overshoot, then
/// a slow, shallow wave runs through them once every [wavePeriod]: enough to
/// feel alive under the hopping mark without competing with it. Only
/// transforms and opacity change per frame; the text is laid out once.
///
/// Each letter is drawn as the whole word with the other letters transparent,
/// so the word keeps its real spacing while every letter moves on its own.
///
/// It stands still while the system "remove animations" setting is on.
class EcomsAnimatedWordmark extends StatefulWidget {
  const EcomsAnimatedWordmark({super.key, this.text = Env.appName, this.style});

  final String text;

  /// Defaults to [EcomsbdType.heroTitle] in ink.
  final TextStyle? style;

  /// The staggered entrance, first letter to last.
  static const Duration entrance = Duration(milliseconds: 900);

  /// One wave through the word, then a pause, repeated.
  static const Duration wavePeriod = Duration(milliseconds: 2800);

  @override
  State<EcomsAnimatedWordmark> createState() => _EcomsAnimatedWordmarkState();
}

class _EcomsAnimatedWordmarkState extends State<EcomsAnimatedWordmark>
    with TickerProviderStateMixin {
  late final AnimationController _entrance = AnimationController(
    vsync: this,
    duration: EcomsAnimatedWordmark.entrance,
  )..addStatusListener(_onEntranceStatus);

  late final AnimationController _wave = AnimationController(
    vsync: this,
    duration: EcomsAnimatedWordmark.wavePeriod,
  );

  bool _initialised = false;
  bool _still = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final still = MediaQuery.maybeDisableAnimationsOf(context) ?? false;
    if (_initialised && still == _still) {
      return;
    }
    final firstBuild = !_initialised;
    _initialised = true;
    _still = still;
    if (still) {
      _wave
        ..stop()
        ..value = 0;
      _entrance
        ..stop()
        ..value = 1;
    } else if (firstBuild) {
      _entrance.forward(from: 0);
    } else {
      // Animations came back on after the entrance would have played.
      _entrance.value = 1;
    }
  }

  void _onEntranceStatus(AnimationStatus status) {
    if (status == AnimationStatus.completed && !_still) {
      _wave.repeat();
    }
  }

  @override
  void dispose() {
    _entrance.dispose();
    _wave.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final style =
        widget.style ??
        EcomsbdType.heroTitle.copyWith(color: EcomsbdColors.ink);
    final fontSize = style.fontSize ?? 14;
    final letters = widget.text.characters.toList();
    final layers = <Widget>[
      for (var i = 0; i < letters.length; i++)
        Text.rich(
          TextSpan(
            children: <InlineSpan>[
              for (var j = 0; j < letters.length; j++)
                TextSpan(
                  text: letters[j],
                  style: j == i ? null : const TextStyle(color: _hidden),
                ),
            ],
          ),
          style: style,
          maxLines: 1,
          softWrap: false,
        ),
    ];
    return Semantics(
      label: widget.text,
      excludeSemantics: true,
      child: RepaintBoundary(
        child: AnimatedBuilder(
          animation: Listenable.merge(<Listenable>[_entrance, _wave]),
          builder: (context, _) {
            final entrance = _entrance.value;
            final wave = _wave.value;
            return Stack(
              clipBehavior: Clip.none,
              children: <Widget>[
                for (var i = 0; i < layers.length; i++)
                  _letter(
                    layers[i],
                    i,
                    layers.length,
                    entrance,
                    wave,
                    fontSize,
                  ),
              ],
            );
          },
        ),
      ),
    );
  }

  Widget _letter(
    Widget layer,
    int index,
    int count,
    double entrance,
    double wave,
    double fontSize,
  ) {
    final rise = _Wordmark.riseAt(entrance, index, count);
    final lift = _Wordmark.liftAt(wave, index);
    return Opacity(
      opacity: _Wordmark.opacityAt(entrance, index, count),
      child: Transform.translate(
        offset: Offset(0, fontSize * (0.36 * (1 - rise) - 0.08 * lift)),
        child: layer,
      ),
    );
  }
}

const Color _hidden = Color(0x00000000);

/// The wordmark's timing, as pure functions of controller progress.
///
/// Entrance, over [EcomsAnimatedWordmark.entrance] (900 ms): each letter takes
/// 480 ms to rise from a third of the font size below its place, with a small
/// overshoot, 60 ms after the one before (closer together for a word too long
/// to fit); it fades in over the first half of that.
///
/// Wave, over [EcomsAnimatedWordmark.wavePeriod] (2800 ms): each letter lifts
/// by 8% of the font size and back over 600 ms, 70 ms after the one before;
/// the word then rests for the remaining ~1.8 s.
class _Wordmark {
  const _Wordmark._();

  static const double _stagger = 60;
  static const double _letterIn = 480;
  static const double _waveStagger = 70;
  static const double _pulse = 600;

  /// 0 below its place, 1 in place (briefly above 1 at the overshoot).
  static double riseAt(double t, int index, int count) =>
      Curves.easeOutBack.transform(_entranceProgress(t, index, count));

  static double opacityAt(double t, int index, int count) => Curves.easeOut
      .transform((_entranceProgress(t, index, count) * 2).clamp(0, 1));

  /// 0 at rest, 1 at the top of the wave.
  static double liftAt(double t, int index) {
    final ms =
        t * EcomsAnimatedWordmark.wavePeriod.inMilliseconds -
        index * _waveStagger;
    if (ms <= 0 || ms >= _pulse) {
      return 0;
    }
    final s = math.sin(math.pi * ms / _pulse);
    return s * s;
  }

  static double _entranceProgress(double t, int index, int count) {
    final total = EcomsAnimatedWordmark.entrance.inMilliseconds;
    final stagger = count > 1
        ? math.min(_stagger, (total - _letterIn) / (count - 1))
        : 0.0;
    return ((t * total - index * stagger) / _letterIn).clamp(0.0, 1.0);
  }
}
