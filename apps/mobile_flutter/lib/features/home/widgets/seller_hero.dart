import 'package:flutter/material.dart';

import '../../../design/tokens.dart';

/// `.hero-banner` — the premium navy seller hero at the top of Home.
///
/// Reproduces the prototype's layered background: a blue bloom top-right, an
/// orange bloom bottom-left, a vertical navy ramp, and the faint concentric
/// rings in the bottom-right corner. Painted rather than composed from images
/// so it scales to any screen without an asset.
class SellerHero extends StatelessWidget {
  const SellerHero({
    required this.shopName,
    required this.subtitle,
    super.key,
    this.eyebrow = 'TODAY · MONEY CONTROL',
    this.chips = const <String>[],
    this.topInset = 0,
  });

  final String shopName;
  final String subtitle;
  final String eyebrow;
  final List<String> chips;

  /// Status-bar height: the hero runs under it, so its content is pushed down.
  final double topInset;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: EdgeInsets.fromLTRB(
        EcomsbdSpacing.lg,
        topInset + EcomsbdSpacing.md,
        EcomsbdSpacing.lg,
        // Deep bottom padding: the money card overlaps the hero's lower edge.
        54,
      ),
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: <Color>[
            EcomsbdColors.navyHeroTop,
            EcomsbdColors.navyHeroUpper,
            EcomsbdColors.navyHeroLower,
            EcomsbdColors.navyHeroBottom,
          ],
          stops: <double>[0.0, 0.42, 0.72, 1.0],
        ),
      ),
      child: Stack(
        children: <Widget>[
          const Positioned(
            right: -60,
            top: -70,
            width: 240,
            height: 240,
            child: _Bloom(color: EcomsbdColors.heroGlowBlue),
          ),
          const Positioned(
            left: -70,
            bottom: -90,
            width: 220,
            height: 220,
            child: _Bloom(color: EcomsbdColors.heroGlowOrange),
          ),
          Positioned(
            right: -110,
            bottom: -120,
            child: CustomPaint(
              size: const Size(220, 220),
              painter: _RingPainter(),
            ),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              _DarkChip(label: eyebrow),
              const SizedBox(height: EcomsbdSpacing.lg),
              Text(
                shopName,
                style: EcomsbdType.heroTitle.copyWith(color: Colors.white),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              const SizedBox(height: 5),
              Text(
                subtitle,
                style: EcomsbdType.caption.copyWith(
                  color: const Color(0xB8FFFFFF),
                ),
              ),
              if (chips.isNotEmpty) ...<Widget>[
                const SizedBox(height: EcomsbdSpacing.sm),
                Wrap(
                  spacing: EcomsbdSpacing.xs,
                  runSpacing: EcomsbdSpacing.xs,
                  children: <Widget>[
                    for (final chip in chips) _DarkChip(label: chip),
                  ],
                ),
              ],
            ],
          ),
        ],
      ),
    );
  }
}

class _DarkChip extends StatelessWidget {
  const _DarkChip({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: const Color(0x14FFFFFF),
        borderRadius: EcomsbdRadii.round,
        border: Border.all(color: const Color(0x1CFFFFFF)),
      ),
      child: Text(
        label,
        style: EcomsbdType.chip.copyWith(color: const Color(0xD6FFFFFF)),
      ),
    );
  }
}

class _Bloom extends StatelessWidget {
  const _Bloom({required this.color});

  final Color color;

  @override
  Widget build(BuildContext context) {
    return IgnorePointer(
      child: DecoratedBox(
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: RadialGradient(
            colors: <Color>[color, color.withValues(alpha: 0)],
          ),
        ),
      ),
    );
  }
}

/// The concentric hairlines in the hero's bottom-right corner.
class _RingPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final base = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1
      ..color = const Color(0x14FFFFFF);
    canvas.drawCircle(center, size.width / 2, base);
    canvas.drawCircle(
      center,
      size.width / 2 + 36,
      base..color = const Color(0x0AFFFFFF),
    );
    canvas.drawCircle(
      center,
      size.width / 2 + 72,
      base..color = const Color(0x06FFFFFF),
    );
  }

  @override
  bool shouldRepaint(_RingPainter oldDelegate) => false;
}
