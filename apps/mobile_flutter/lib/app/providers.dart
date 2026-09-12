import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import '../core/api/api_client.dart';
import '../core/storage/token_store.dart';
import '../data/auth/auth_controller.dart';
import '../data/auth/auth_repository.dart';
import '../data/auth/provider_sign_in.dart';
import '../data/local/database.dart';

/// The application's provider graph.
///
/// Kept in one file so the wiring is readable end to end: storage, database,
/// HTTP client, repositories, controllers. Tests override the leaves (database,
/// token store, API client) and get the rest for free.

final uuidProvider = Provider<Uuid>((ref) => const Uuid());

/// The local Drift database.
///
/// Overridden with an in-memory instance in tests.
final databaseProvider = Provider<EcomsbdDatabase>((ref) {
  final database = EcomsbdDatabase();
  ref.onDispose(database.close);
  return database;
});

final tokenStoreProvider = Provider<TokenStore>((ref) => TokenStore());

/// Reported to the server at sign-in and used to key the device record.
final appVersionProvider = StateProvider<String?>((ref) => null);

final authRepositoryProvider = Provider<AuthRepository>((ref) {
  final repository = AuthRepository(tokenStore: ref.watch(tokenStoreProvider));
  // The client needs the repository as its SessionProvider, and the repository
  // needs the client to make calls; the cycle is closed here, once.
  repository.client = ApiClient(
    sessionProvider: repository,
    appVersion: ref.watch(appVersionProvider),
  );
  ref.onDispose(repository.dispose);
  return repository;
});

/// The shared HTTP client.
///
/// Returns the instance the auth repository already holds rather than building
/// another. Two clients would each run their own token refresh, rotate the
/// refresh token twice and trip the server's reuse detection.
final apiClientProvider = Provider<ApiClient>((ref) {
  return ref.watch(authRepositoryProvider).api;
});

final providerSignInProvider = Provider<ProviderSignIn>(
  (ref) => NativeProviderSignIn(),
);

final authControllerProvider = StateNotifierProvider<AuthController, AuthState>(
  (ref) {
    return AuthController(
      ref.watch(authRepositoryProvider),
      providerSignIn: ref.watch(providerSignInProvider),
    );
  },
);

/// Count of locally created or edited records not yet accepted by the server.
///
/// Drives the offline banner (master spec section 127: a seller can see which
/// records are not yet on the server).
final pendingMutationCountProvider = StreamProvider<int>((ref) {
  return ref.watch(databaseProvider).watchPendingCount();
});

/// Whether the last API attempt failed for lack of a connection.
///
/// A real connectivity subscription arrives with the sync engine in Phase B;
/// until then this is driven by API results, which is what actually matters —
/// a device can be on wifi with no route to the server.
final isOfflineProvider = StateProvider<bool>((ref) => false);

/// Devices flagged as low-end, used to switch off expensive blur.
final isLowEndDeviceProvider = StateProvider<bool>((ref) => false);

@visibleForTesting
List<Override> testOverrides({
  EcomsbdDatabase? database,
  TokenStore? tokenStore,
}) => <Override>[
  if (database != null) databaseProvider.overrideWithValue(database),
  if (tokenStore != null) tokenStoreProvider.overrideWithValue(tokenStore),
];
