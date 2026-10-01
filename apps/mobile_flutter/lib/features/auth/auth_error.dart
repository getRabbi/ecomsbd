import 'package:flutter/widgets.dart';

import '../../core/api/api_error.dart';
import '../../l10n/app_strings.dart';

/// Auth copy is selected by stable codes; raw server/SDK messages stay private.
String authErrorMessage(BuildContext context, ApiError error) =>
    switch (error.code) {
      'INVALID_CREDENTIALS' => context.tr('autherr.invalidCredentials'),
      ApiErrorCode.offline => context.tr('autherr.offline'),
      ApiErrorCode.timeout => context.tr('autherr.timeout'),
      // Android cannot tell a dismissal from a rejected OAuth registration,
      // so this copy does not claim the seller cancelled anything.
      'GOOGLE_INCOMPLETE' => context.tr('autherr.googleIncomplete'),
      'GOOGLE_CANCELLED' => context.tr('autherr.googleCancelled'),
      'APPLE_CANCELLED' => context.tr('autherr.appleCancelled'),
      'GOOGLE_UNAVAILABLE' => context.tr('autherr.googleUnavailable'),
      'APPLE_UNAVAILABLE' => context.tr('autherr.appleUnavailable'),
      'EMAIL_NOT_VERIFIED' => context.tr('autherr.emailNotVerified'),
      'EMAIL_ALREADY_REGISTERED' => context.tr(
        'autherr.emailAlreadyRegistered',
      ),
      'IDENTITY_LINK_REFUSED' ||
      ApiErrorCode.conflict => context.tr('autherr.identityLinkRefused'),
      ApiErrorCode.invalidToken ||
      ApiErrorCode.tokenExpired => context.tr('autherr.linkExpired'),
      ApiErrorCode.rateLimited => context.tr('autherr.rateLimited'),
      ApiErrorCode.validation => context.tr('autherr.validation'),
      ApiErrorCode.featureDisabled || ApiErrorCode.serviceUnavailable =>
        context.tr('autherr.methodUnavailable'),
      ApiErrorCode.forbidden => context.tr('autherr.forbidden'),
      _ => context.tr('autherr.generic'),
    };

/// The code shown under [error] so a tester can say where sign-in failed,
/// such as `APPLE_SUPABASE_REJECTED 400 provider_disabled`.
///
/// Release builds keep no other trace of a provider failure. Null for other
/// errors, and for a cancellation, which needs no explaining.
String? authErrorDiagnostic(ApiError error) =>
    error.code.endsWith('_CANCELLED') ? null : error.diagnostic;
