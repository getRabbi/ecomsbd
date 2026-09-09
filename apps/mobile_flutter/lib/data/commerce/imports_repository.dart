import '../../core/api/api_error.dart';
import 'models.dart';
import 'repository_support.dart';

/// The generic import pipeline (master spec sections 7.3, 98).
///
/// Three deliberate steps: upload and detect, dry-run, commit. The dry run is
/// not optional — an import is a bulk write a seller cannot easily undo, so
/// nothing is created before they have seen the counts and the bad rows.
///
/// Nothing here is cached or queued. An import needs a connection, and an
/// import that appeared to work offline would be a lie about hundreds of
/// records.
class ImportsRepository extends CachingRepository {
  ImportsRepository({
    required super.api,
    required super.db,
    required this.tenantId,
  });

  @override
  final String? tenantId;

  /// Upload a file and get the detected headers plus a suggested mapping.
  ///
  /// The mapping is a suggestion the seller can correct before the dry run,
  /// never a decision made on their behalf.
  Future<ImportBatch> upload({
    required List<int> bytes,
    required String filename,
    required String template,
  }) async {
    final json = await api.postFile(
      '/imports',
      bytes: bytes,
      filename: filename,
      fields: <String, dynamic>{'template': template},
    );
    return ImportBatch.fromJson(json);
  }

  /// Validate every row without writing anything.
  Future<ImportBatch> dryRun(
    String importId, {
    Map<String, String>? columnMapping,
  }) async {
    final json = await api.post(
      '/imports/$importId/dry-run',
      body: <String, dynamic>{
        if (columnMapping != null) 'column_mapping': columnMapping,
      },
    );
    return ImportBatch.fromJson(json);
  }

  /// Create everything the dry run marked importable.
  ///
  /// A row that fails at creation is marked invalid with its reason and the
  /// rest continue.
  Future<({ImportBatch batch, int created, int skipped})> commit(
    String importId,
  ) async {
    final json = await api.post('/imports/$importId/commit');
    return (
      batch: ImportBatch.fromJson(json['import_batch'] as Map<String, dynamic>),
      created: json['created_count'] as int? ?? 0,
      skipped: json['skipped_count'] as int? ?? 0,
    );
  }

  Future<ImportBatch> get(String importId) async {
    final json = await api.get('/imports/$importId');
    return ImportBatch.fromJson(json);
  }

  /// The per-row outcome, including the raw values the file contained.
  ///
  /// This is the error report a seller fixing twelve bad rows needs: which
  /// twelve, and why.
  Future<List<ImportRowReport>> rows(
    String importId, {
    String? status,
    int limit = 100,
  }) async {
    final items = await api.getList(
      '/imports/$importId/rows',
      query: <String, dynamic>{
        'limit': limit,
        if (status != null) 'status': status,
      },
    );
    return <ImportRowReport>[
      for (final item in items)
        ImportRowReport.fromJson(item as Map<String, dynamic>),
    ];
  }

  /// True when the failure means "you need a connection for this".
  static bool needsConnection(Object error) =>
      error is ApiError && error.isOffline;
}
