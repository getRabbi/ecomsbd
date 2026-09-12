import '../../core/api/api_error.dart';

/// Auth copy is selected by stable codes; raw server/SDK messages stay private.
String authErrorMessage(ApiError error) => switch (error.code) {
  'INVALID_CREDENTIALS' =>
    'That email and password do not match. Please try again.',
  ApiErrorCode.offline =>
    'No internet connection. Check your connection and try again.',
  'GOOGLE_CANCELLED' => 'Google sign-in was cancelled. You can try again.',
  'APPLE_CANCELLED' => 'Apple sign-in was cancelled. You can try again.',
  'GOOGLE_UNAVAILABLE' =>
    'Google sign-in is unavailable right now. Use email or try again later.',
  'APPLE_UNAVAILABLE' =>
    'Apple sign-in is unavailable on this device. Use Google or email.',
  'EMAIL_NOT_VERIFIED' =>
    'Verify your email using the link in your inbox, then sign in.',
  'EMAIL_ALREADY_REGISTERED' =>
    'An account already uses this email. Sign in or reset your password.',
  'IDENTITY_LINK_REFUSED' || ApiErrorCode.conflict =>
    'This sign-in method belongs to another account. Use your original sign-in method.',
  ApiErrorCode.invalidToken || ApiErrorCode.tokenExpired =>
    'This link or sign-in has expired. Request a new link or sign in again.',
  ApiErrorCode.rateLimited =>
    'Too many attempts. Please wait a while before trying again.',
  ApiErrorCode.validation =>
    'Check your email and password. Use 10–200 characters and avoid common passwords.',
  ApiErrorCode.featureDisabled || ApiErrorCode.serviceUnavailable =>
    'This sign-in method is unavailable right now. Please try another method.',
  ApiErrorCode.forbidden =>
    'This account cannot sign in. Contact support for help.',
  _ => 'Sign-in could not be completed. Please try again.',
};
