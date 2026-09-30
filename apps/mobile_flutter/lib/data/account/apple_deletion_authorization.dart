import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:sign_in_with_apple/sign_in_with_apple.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../../core/env.dart';

/// The seller dismissed the Apple sheet shown before closing their account.
class AppleDeletionCancelled implements Exception {
  const AppleDeletionCancelled();
}

/// A fresh Sign in with Apple authorization code for account deletion.
///
/// App Store Review Guideline 5.1.1(v): deleting an account that signed in
/// with Apple must revoke its Apple tokens. Supabase keeps none, so the seller
/// authorizes once more and the server exchanges this code and revokes the
/// result. Null when the seller did not sign in with Apple, or when Apple
/// cannot produce a code; deletion then goes ahead without revocation rather
/// than being blocked by it. Throws [AppleDeletionCancelled] when the seller
/// dismisses the sheet, so the deletion stops too.
typedef AppleDeletionAuthorizer = Future<String?> Function();

final appleDeletionAuthorizerProvider = Provider<AppleDeletionAuthorizer>(
  (ref) => _appleAuthorizationCode,
);

Future<String?> _appleAuthorizationCode() async {
  if (!Env.appleSignInEnabled) return null;
  final metadata = Supabase.instance.client.auth.currentUser?.appMetadata;
  final providers = metadata?['providers'];
  final signedInWithApple =
      metadata?['provider'] == 'apple' ||
      (providers is List && providers.contains('apple'));
  if (!signedInWithApple) return null;
  try {
    if (!await SignInWithApple.isAvailable()) return null;
    final credential = await SignInWithApple.getAppleIDCredential(
      scopes: const <AppleIDAuthorizationScopes>[],
    );
    return credential.authorizationCode.isEmpty
        ? null
        : credential.authorizationCode;
  } on SignInWithAppleAuthorizationException catch (error) {
    if (error.code == AuthorizationErrorCode.canceled) {
      throw const AppleDeletionCancelled();
    }
    return null;
  } on Object {
    return null;
  }
}
