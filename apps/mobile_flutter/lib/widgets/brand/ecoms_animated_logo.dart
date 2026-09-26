import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../core/env.dart';
import '../../design/tokens.dart';

/// The ecomsbd brand mark, moving like a small soft-plastic object.
///
/// Renders the supplied mark ([assetPath]) as delivered, with only its white
/// backdrop removed: no recolouring, no redrawing, no baked-in effects. The
/// depth comes only from motion — a squash before take-off, a short hop with a
/// slight perspective tilt, a squash on landing and a springy settle — over a
/// separate contact shadow that shrinks and fades as the mark rises. It never
/// spins or flips: the artwork is flat, and showing its edge would give that
/// away.
///
/// * `animate: false` — the mark at rest.
/// * `animate: true, looping: true` — the hop repeats, for loading states.
///   With a [rest], the mark pauses between hops, for everyday UI.
/// * `animate: true, looping: false` — fades in, hops once, then rests.
///
/// It always rests while the system "remove animations" setting is on.
///
/// [size] is the side of the square asset canvas. The artwork carries
/// transparent padding (8–12% per side), so the visible mark is smaller than
/// [size]. That padding absorbs most of the hop, but at the top of it the mark
/// can paint up to ~4% of [size] above the box: leave that much room before
/// any clip.
class EcomsAnimatedLogo extends StatefulWidget {
  const EcomsAnimatedLogo({
    super.key,
    this.size = 110,
    this.animate = true,
    this.looping = true,
    this.rest = Duration.zero,
    this.semanticLabel = Env.appName,
  });

  /// The brand mark on transparency, derived from the delivered
  /// `assets/icon/logo_mark.png` by removing only its white backdrop.
  static const String assetPath = 'assets/icon/logo_mark_transparent.png';

  /// One hop, from rest to rest.
  static const Duration period = Duration(milliseconds: 1400);

  final double size;
  final bool animate;
  final bool looping;

  /// While looping, how long the mark stands still between hops; zero hops
  /// back to back. A rest also comes before the first hop, so the mark does
  /// not jump the moment its screen appears. Nothing is drawn while it rests.
  final Duration rest;

  /// Read by screen readers. Null hides the mark from them.
  final String? semanticLabel;

  @override
  State<EcomsAnimatedLogo> createState() => _EcomsAnimatedLogoState();
}

class _EcomsAnimatedLogoState extends State<EcomsAnimatedLogo>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: EcomsAnimatedLogo.period,
  );

  bool _initialised = false;
  bool _reduceMotion = false;

  /// Counts down a [EcomsAnimatedLogo.rest] before the next hop.
  Timer? _restTimer;

  /// Whether the current run is a one-shot entrance, which fades in.
  bool _fadeIn = false;

  bool get _moving => widget.animate && !_reduceMotion;

  bool get _resting => widget.looping && widget.rest > Duration.zero;

  @override
  void initState() {
    super.initState();
    _controller.addStatusListener((status) {
      if (status == AnimationStatus.completed && _moving && _resting) {
        _restBeforeHop();
      }
    });
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final reduceMotion = MediaQuery.maybeDisableAnimationsOf(context) ?? false;
    if (_initialised && reduceMotion == _reduceMotion) {
      return;
    }
    final firstBuild = !_initialised;
    _initialised = true;
    _reduceMotion = reduceMotion;
    _syncController(replay: firstBuild);
  }

  @override
  void didUpdateWidget(EcomsAnimatedLogo oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.animate != oldWidget.animate ||
        widget.looping != oldWidget.looping ||
        widget.rest != oldWidget.rest) {
      _syncController(replay: widget.animate && !oldWidget.animate);
    }
  }

  /// Start, stop or redirect the controller to match the current settings.
  ///
  /// [replay] is true when the mark has just been asked to animate, so a
  /// one-shot entrance plays from the start.
  void _syncController({required bool replay}) {
    if (!_moving || !_resting) {
      _restTimer?.cancel();
    }
    if (!_moving) {
      _fadeIn = false;
      _controller
        ..stop()
        ..value = 0;
      return;
    }
    if (_resting) {
      _fadeIn = false;
      if (_controller.isAnimating) {
        // Land this hop; the status listener then rests before the next.
        _controller.forward();
      } else if (!(_restTimer?.isActive ?? false)) {
        _restBeforeHop();
      }
    } else if (widget.looping) {
      _fadeIn = false;
      if (!_controller.isAnimating) {
        _controller.repeat();
      }
    } else if (replay) {
      _fadeIn = true;
      _controller.forward(from: 0);
    } else if (_controller.isAnimating) {
      // Looping was switched off mid-hop: land this hop, then rest.
      _controller.forward();
    } else {
      // Animations came back on after the entrance would have played. An
      // entrance that late would only distract, so stay at rest.
      _controller.value = 1;
    }
  }

  /// Wait out one rest, then hop once. A timer rather than a padded
  /// animation, so no frames are scheduled while the mark stands still.
  void _restBeforeHop() {
    _restTimer?.cancel();
    _restTimer = Timer(widget.rest, () {
      if (mounted && _moving && _resting) {
        _controller.forward(from: 0);
      }
    });
  }

  @override
  void dispose() {
    _restTimer?.cancel();
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final size = widget.size;
    final pixelRatio = MediaQuery.maybeDevicePixelRatioOf(context) ?? 1;
    return RepaintBoundary(
      child: SizedBox.square(
        dimension: size,
        child: Image.asset(
          EcomsAnimatedLogo.assetPath,
          width: size,
          height: size,
          fit: BoxFit.contain,
          // The source is 1416 px square. Decoding at display size keeps an
          // ~8 MB bitmap off the low-end phones this app targets.
          cacheWidth: (size * pixelRatio).ceil(),
          // Smooth sampling while the mark is scaled and tilted.
          filterQuality: FilterQuality.medium,
          semanticLabel: widget.semanticLabel,
          excludeFromSemantics: widget.semanticLabel == null,
          frameBuilder: _buildFrame,
        ),
      ),
    );
  }

  Widget _buildFrame(
    BuildContext context,
    Widget image,
    int? frame,
    bool wasSynchronouslyLoaded,
  ) {
    final size = widget.size;
    final Widget mark = _moving
        ? AnimatedBuilder(
            animation: _controller,
            // The decoded image passes through untouched; only the pose and
            // the shadow are rebuilt each frame.
            child: image,
            builder: (context, child) {
              final t = _controller.value;
              final posed = _PosedMark(
                pose: _Pose.at(t),
                size: size,
                image: child!,
              );
              return _fadeIn
                  ? Opacity(
                      opacity: _Motion.entrance.transform(t),
                      alwaysIncludeSemantics: true,
                      child: posed,
                    )
                  : posed;
            },
          )
        : _PosedMark(pose: _Pose.rest, size: size, image: image);
    if (wasSynchronouslyLoaded) {
      return mark;
    }
    // The first decode lands a frame or two after the screen appears. Ease the
    // mark and its shadow in together rather than popping. The label is
    // announced meanwhile: a screen reader should not miss "Loading" because
    // the bitmap is still decoding.
    return AnimatedOpacity(
      opacity: frame == null ? 0 : 1,
      alwaysIncludeSemantics: true,
      duration: const Duration(milliseconds: 220),
      curve: Curves.easeOut,
      child: mark,
    );
  }
}

/// The mark and its contact shadow in one pose.
class _PosedMark extends StatelessWidget {
  const _PosedMark({
    required this.pose,
    required this.size,
    required this.image,
  });

  final _Pose pose;
  final double size;
  final Widget image;

  @override
  Widget build(BuildContext context) {
    return Stack(
      clipBehavior: Clip.none,
      fit: StackFit.expand,
      children: <Widget>[
        CustomPaint(
          painter: _ContactShadowPainter(
            lift: pose.lift.clamp(0.0, 1.0),
            squash: pose.squash,
          ),
        ),
        Transform(transform: pose.transformFor(size), child: image),
      ],
    );
  }
}

/// Hop height as a fraction of size: 12 dp at the default 110.
const double _hopHeight = 0.11;

/// Where the bottom of the bag sits in the asset canvas. Squash and stretch
/// pivot here, so the mark presses into the ground instead of shrinking toward
/// its middle.
const double _baseline = 0.92;

/// Horizontal scale at the landing squash; the deepest point of any squash.
const double _landingSquashX = 1.04;

const double _radiansPerDegree = math.pi / 180;

/// Depth for the tilt, centred on the mark.
final Matrix4 _perspective = Matrix4.identity()..setEntry(3, 2, 0.0012);

/// Where the mark is at one instant of the hop.
@immutable
class _Pose {
  const _Pose({
    this.lift = 0,
    this.scaleX = 1,
    this.scaleY = 1,
    this.rotateY = 0,
    this.rotateZ = 0,
  });

  factory _Pose.at(double t) => _Pose(
    lift: _Motion.lift.transform(t),
    scaleX: _Motion.scaleX.transform(t),
    scaleY: _Motion.scaleY.transform(t),
    rotateY: _Motion.rotateY.transform(t) * _radiansPerDegree,
    rotateZ: _Motion.rotateZ.transform(t) * _radiansPerDegree,
  );

  static const _Pose rest = _Pose();

  /// Height off the ground as a fraction of the hop. Below zero while the
  /// mark sinks into the anticipation squash.
  final double lift;

  final double scaleX;
  final double scaleY;

  /// Radians.
  final double rotateY;

  /// Radians.
  final double rotateZ;

  /// How hard the mark is pressed into the ground: 0 at rest, 1 at landing.
  double get squash => ((scaleX - 1) / (_landingSquashX - 1)).clamp(0.0, 1.0);

  /// This pose over a [size]-sided box.
  ///
  /// Lift and tilt act about the centre; squash and stretch act about the base.
  Matrix4 transformFor(double size) {
    final half = size / 2;
    final base = size * _baseline;
    final matrix = Matrix4.translationValues(
      half,
      half - lift * size * _hopHeight,
      0,
    );
    if (rotateY != 0 || rotateZ != 0) {
      matrix
        ..multiply(_perspective)
        ..rotateY(rotateY)
        ..rotateZ(rotateZ);
    }
    return matrix
      ..translateByDouble(0, base - half, 0, 1)
      ..scaleByDouble(scaleX, scaleY, 1, 1)
      ..translateByDouble(-half, -base, 0, 1);
  }
}

/// The hop as keyframe tracks over one [EcomsAnimatedLogo.period] (1400 ms).
///
/// Each track is a [TweenSequence] whose weights are segment lengths in
/// milliseconds, so every row reads "reach this value, over this long, with
/// this easing".
///
///      0–120   idle           at rest
///    120–300   anticipation   squash to x1.035 / y0.96, sinks slightly
///    300–640   jump           rises 11% of size, stretches, tilt begins
///    300–900   air tilt       rotateY 0 → -6° → +6°, rotateZ 0 → -2° → +2°
///    640–920   fall           accelerates back to the ground
///    920–1010  landing        squash to x1.04 / y0.955
///   1010–1300  settle         rebounds past rest and springs back; the tilt
///                             eases out with a small overshoot
///   1300–1400  rest
class _Motion {
  const _Motion._();

  static final Animatable<double> lift = _track(0, <_Segment>[
    (to: 0, ms: 120, curve: Curves.linear),
    (to: -0.12, ms: 180, curve: Curves.easeInOut),
    (to: 1, ms: 340, curve: Curves.easeOutCubic),
    (to: 0, ms: 280, curve: Curves.easeInQuad),
    (to: 0, ms: 480, curve: Curves.linear),
  ]);

  static final Animatable<double> scaleX = _track(1, <_Segment>[
    (to: 1, ms: 120, curve: Curves.linear),
    (to: 1.035, ms: 180, curve: Curves.easeInOut),
    (to: 0.985, ms: 100, curve: Curves.easeOut),
    (to: 1, ms: 240, curve: Curves.easeInOut),
    (to: 0.99, ms: 280, curve: Curves.easeIn),
    (to: _landingSquashX, ms: 90, curve: Curves.easeOutCubic),
    (to: 0.992, ms: 150, curve: Curves.easeInOut),
    (to: 1, ms: 140, curve: Curves.easeOut),
    (to: 1, ms: 100, curve: Curves.linear),
  ]);

  static final Animatable<double> scaleY = _track(1, <_Segment>[
    (to: 1, ms: 120, curve: Curves.linear),
    (to: 0.96, ms: 180, curve: Curves.easeInOut),
    (to: 1.03, ms: 100, curve: Curves.easeOut),
    (to: 1, ms: 240, curve: Curves.easeInOut),
    (to: 1.012, ms: 280, curve: Curves.easeIn),
    (to: 0.955, ms: 90, curve: Curves.easeOutCubic),
    (to: 1.012, ms: 150, curve: Curves.easeInOut),
    (to: 1, ms: 140, curve: Curves.easeOut),
    (to: 1, ms: 100, curve: Curves.linear),
  ]);

  /// Degrees.
  static final Animatable<double> rotateY = _track(0, <_Segment>[
    (to: 0, ms: 300, curve: Curves.linear),
    (to: -6, ms: 200, curve: Curves.easeOutSine),
    (to: 6, ms: 360, curve: Curves.easeInOutSine),
    (to: 0, ms: 240, curve: Curves.easeOutBack),
    (to: 0, ms: 300, curve: Curves.linear),
  ]);

  /// Degrees. Trails [rotateY] by 40 ms so the tilt reads as follow-through.
  static final Animatable<double> rotateZ = _track(0, <_Segment>[
    (to: 0, ms: 340, curve: Curves.linear),
    (to: -2, ms: 200, curve: Curves.easeOutSine),
    (to: 2, ms: 360, curve: Curves.easeInOutSine),
    (to: 0, ms: 260, curve: Curves.easeOutBack),
    (to: 0, ms: 240, curve: Curves.linear),
  ]);

  /// Opacity for a one-shot entrance: in over the first 200 ms.
  static final Animatable<double> entrance = CurveTween(
    curve: const Interval(0, 200 / 1400, curve: Curves.easeOut),
  );
}

typedef _Segment = ({double to, int ms, Curve curve});

Animatable<double> _track(double start, List<_Segment> segments) {
  assert(
    segments.fold<int>(0, (total, segment) => total + segment.ms) ==
        EcomsAnimatedLogo.period.inMilliseconds,
    'A keyframe track must span exactly one period.',
  );
  var from = start;
  final items = <TweenSequenceItem<double>>[];
  for (final segment in segments) {
    items.add(
      TweenSequenceItem<double>(
        tween: Tween<double>(
          begin: from,
          end: segment.to,
        ).chain(CurveTween(curve: segment.curve)),
        weight: segment.ms.toDouble(),
      ),
    );
    from = segment.to;
  }
  return TweenSequence<double>(items);
}

/// Where the contact shadow sits and how big it is, as fractions of size.
const double _shadowCentreY = 0.93;
const double _shadowWidth = 0.60;
const double _shadowAspect = 0.14;
const double _shadowRestAlpha = 0.22;

/// The soft ellipse the mark casts on the ground.
///
/// Drawn separately from the artwork, which has no shadow of its own. It
/// narrows and fades as the mark rises, and spreads and darkens slightly under
/// a squash — most of what makes the hop read as physical.
class _ContactShadowPainter extends CustomPainter {
  const _ContactShadowPainter({required this.lift, required this.squash});

  /// 0 on the ground, 1 at the top of the hop.
  final double lift;

  /// 0 at rest, 1 fully squashed.
  final double squash;

  @override
  void paint(Canvas canvas, Size size) {
    final spread = (1 - 0.26 * lift) * (1 + 0.10 * squash);
    final alpha = _shadowRestAlpha * (1 - 0.55 * lift) * (1 + 0.25 * squash);
    final radius = size.width * _shadowWidth / 2 * spread;
    const color = EcomsbdColors.brandMarkShadow;
    final paint = Paint()
      ..shader = RadialGradient(
        colors: <Color>[
          color.withValues(alpha: alpha),
          color.withValues(alpha: alpha * 0.45),
          color.withValues(alpha: 0),
        ],
        stops: const <double>[0, 0.5, 1],
      ).createShader(Rect.fromCircle(center: Offset.zero, radius: radius));
    canvas
      ..save()
      ..translate(size.width / 2, size.height * _shadowCentreY)
      // RadialGradient is always circular; flatten the circle into an ellipse.
      ..scale(1, _shadowAspect)
      ..drawCircle(Offset.zero, radius, paint)
      ..restore();
  }

  @override
  bool shouldRepaint(_ContactShadowPainter oldDelegate) =>
      oldDelegate.lift != lift || oldDelegate.squash != squash;
}
