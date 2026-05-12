<?php

declare(strict_types=1);

use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Uuid\UuidRegistry;
use OpenEMR\Modules\AiAgent\Service\BearerTokenMinter;

const DEFAULT_OPENEMR_ROOT = '/var/www/localhost/htdocs/openemr';
const MODULE_NAMESPACE = 'OpenEMR\\Modules\\AiAgent\\';

/**
 * Dev helper for RedLens live-target testing.
 *
 * This script is intended to run inside the OpenEMR container where OpenEMR's
 * PHP runtime, DB settings, OAuth keys, and module files are available.
 */

$options = parseOptions($argv ?? []);

if (isset($options['help'])) {
    usage(0);
}

ob_start();

$openemrRoot = rtrim((string) ($options['openemr-root'] ?? DEFAULT_OPENEMR_ROOT), '/');
$scope = strtolower((string) ($options['scope'] ?? 'chat'));

if (!in_array($scope, ['chat', 'brief'], true)) {
    fail('--scope must be either "chat" or "brief".');
}

bootstrapOpenEmr($openemrRoot);

$user = resolveUser($options);
$patient = isset($options['pid']) ? resolvePatient((string) $options['pid']) : null;
$scopes = $scope === 'brief'
    ? BearerTokenMinter::FHIR_READ_SCOPES
    : BearerTokenMinter::CHAT_READ_SCOPES;

try {
    $token = BearerTokenMinter::default()->mintForUser($user['uuid'], $scopes);
} catch (Throwable $e) {
    fail('Token mint failed: ' . $e->getMessage());
}

$bootstrapOutput = ob_get_clean();
if (getenv('REDLENS_TOKEN_HELPER_DEBUG') && $bootstrapOutput !== false && $bootstrapOutput !== '') {
    fwrite(STDERR, $bootstrapOutput);
}

$result = [
    'token_type' => 'Bearer',
    'expires_in_seconds' => 300,
    'scope_profile' => $scope,
    'user_id' => $user['id'],
    'username' => $user['username'],
    'user_uuid' => $user['uuid'],
    'patient_pid' => $patient['pid'] ?? null,
    'patient_uuid' => $patient['uuid'] ?? null,
    'fhir_base_url' => getenv('AI_AGENT_FHIR_BASE_URL') ?: 'http://openemr/apis/default/fhir',
    'internal_auth_secret' => getenv('INTERNAL_AUTH_SECRET') ?: 'dev-internal-auth-secret',
    'access_token' => $token,
];

if (isset($options['env'])) {
    echo 'export OPENEMR_INTERNAL_AUTH_SECRET=' . shellQuote($result['internal_auth_secret']) . PHP_EOL;
    echo 'export OPENEMR_BEARER_TOKEN=' . shellQuote($result['access_token']) . PHP_EOL;
    echo 'export OPENEMR_FHIR_BASE_URL=' . shellQuote($result['fhir_base_url']) . PHP_EOL;
    if ($result['patient_uuid'] !== null) {
        echo 'export OPENEMR_PATIENT_UUID=' . shellQuote((string) $result['patient_uuid']) . PHP_EOL;
    }
    exit(0);
}

if (isset($options['json'])) {
    echo json_encode($result, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . PHP_EOL;
    exit(0);
}

fwrite(STDERR, "Minted {$scope} token for {$user['username']} ({$user['uuid']}).\n");
if ($patient !== null) {
    fwrite(STDERR, "Resolved patient pid {$patient['pid']} to {$patient['uuid']}.\n");
}
echo $token . PHP_EOL;

function usage(int $exitCode): never
{
    $message = <<<'TXT'
Usage:
  php mint_openemr_token.php --username admin [--pid 1] [--scope chat] [--env]
  php mint_openemr_token.php --user-id 1 [--pid 1] [--json]
  php mint_openemr_token.php --user-uuid <uuid> [--scope brief]

Options:
  --username       OpenEMR users.username to mint for.
  --user-id        OpenEMR users.id to mint for.
  --user-uuid      Existing OpenEMR user UUID to mint for.
  --pid            Optional patient_data.pid; prints the matching patient UUID.
  --scope          chat (default) or brief.
  --env            Print shell exports for RedLens.
  --json           Print token metadata as JSON.
TXT;
    fwrite($exitCode === 0 ? STDOUT : STDERR, $message . PHP_EOL);
    exit($exitCode);
}

/**
 * @param list<string> $argv
 * @return array<string, string|bool>
 */
function parseOptions(array $argv): array
{
    $options = [];
    $valueOptions = [
        'openemr-root' => true,
        'user-id' => true,
        'username' => true,
        'user-uuid' => true,
        'pid' => true,
        'scope' => true,
    ];
    $flagOptions = [
        'json' => true,
        'env' => true,
        'help' => true,
    ];

    for ($i = 1; $i < count($argv); $i++) {
        $arg = $argv[$i];
        if ($arg === '--') {
            continue;
        }
        if (!str_starts_with($arg, '--')) {
            fail("Unexpected argument: {$arg}");
        }

        $raw = substr($arg, 2);
        $name = $raw;
        $value = null;
        if (str_contains($raw, '=')) {
            [$name, $value] = explode('=', $raw, 2);
        }

        if (isset($flagOptions[$name])) {
            $options[$name] = true;
            continue;
        }

        if (!isset($valueOptions[$name])) {
            fail("Unknown option: --{$name}");
        }

        if ($value === null) {
            $i++;
            if (!isset($argv[$i]) || str_starts_with($argv[$i], '--')) {
                fail("Missing value for --{$name}");
            }
            $value = $argv[$i];
        }
        $options[$name] = $value;
    }

    return $options;
}

function bootstrapOpenEmr(string $openemrRoot): void
{
    $globals = $openemrRoot . '/interface/globals.php';
    $moduleSrc = $openemrRoot . '/interface/modules/custom_modules/oe-module-ai-agent/src';

    if (!is_file($globals)) {
        fail("OpenEMR globals.php not found at {$globals}");
    }
    if (!is_dir($moduleSrc)) {
        fail("AI Agent module src not found at {$moduleSrc}");
    }

    $ignoreAuth = true;
    $sessionAllowWrite = true;
    $GLOBALS['ignoreAuth'] = $ignoreAuth;
    $GLOBALS['sessionAllowWrite'] = $sessionAllowWrite;
    $_GET['site'] ??= 'default';
    $_REQUEST['site'] ??= $_GET['site'];
    $_SERVER['HTTP_HOST'] ??= 'localhost';
    $_SERVER['SERVER_NAME'] ??= 'localhost';
    $_SERVER['SERVER_PORT'] ??= '443';
    $_SERVER['HTTPS'] ??= 'on';
    $_SERVER['REQUEST_URI'] ??= '/';
    $_SERVER['REQUEST_METHOD'] ??= 'GET';

    require_once $globals;

    spl_autoload_register(
        static function (string $class) use ($moduleSrc): void {
            if (!str_starts_with($class, MODULE_NAMESPACE)) {
                return;
            }
            $relative = substr($class, strlen(MODULE_NAMESPACE));
            $path = $moduleSrc . '/' . str_replace('\\', '/', $relative) . '.php';
            if (is_file($path)) {
                require_once $path;
            }
        }
    );
}

/**
 * @param array<string, string|false> $options
 * @return array{id:int|null, username:string|null, uuid:string}
 */
function resolveUser(array $options): array
{
    if (!empty($options['user-uuid'])) {
        $uuid = (string) $options['user-uuid'];
        $rows = QueryUtils::fetchRecords(
            'SELECT id, username, uuid FROM users WHERE uuid = ? LIMIT 1',
            [UuidRegistry::uuidToBytes($uuid)],
            true,
        );
        return [
            'id' => isset($rows[0]['id']) ? (int) $rows[0]['id'] : null,
            'username' => $rows[0]['username'] ?? null,
            'uuid' => $uuid,
        ];
    }

    if (!empty($options['user-id'])) {
        $rows = QueryUtils::fetchRecords(
            'SELECT id, username, uuid FROM users WHERE id = ? LIMIT 1',
            [(int) $options['user-id']],
            true,
        );
    } elseif (!empty($options['username'])) {
        $rows = QueryUtils::fetchRecords(
            'SELECT id, username, uuid FROM users WHERE username = ? LIMIT 1',
            [(string) $options['username']],
            true,
        );
    } else {
        usage(1);
    }

    if (empty($rows[0]['uuid'])) {
        fail('No matching OpenEMR user with a UUID was found.');
    }

    return [
        'id' => (int) $rows[0]['id'],
        'username' => (string) $rows[0]['username'],
        'uuid' => UuidRegistry::uuidToString($rows[0]['uuid']),
    ];
}

/**
 * @return array{pid:int, uuid:string}
 */
function resolvePatient(string $pid): array
{
    if (!ctype_digit($pid)) {
        fail('--pid must be numeric.');
    }
    $rows = QueryUtils::fetchRecords(
        'SELECT pid, uuid FROM patient_data WHERE pid = ? LIMIT 1',
        [(int) $pid],
        true,
    );
    if (empty($rows[0]['uuid'])) {
        fail("No patient_data row with a UUID was found for pid {$pid}.");
    }

    return [
        'pid' => (int) $rows[0]['pid'],
        'uuid' => UuidRegistry::uuidToString($rows[0]['uuid']),
    ];
}

function shellQuote(string $value): string
{
    return "'" . str_replace("'", "'\"'\"'", $value) . "'";
}

function fail(string $message): never
{
    while (ob_get_level() > 0) {
        ob_end_clean();
    }
    fwrite(STDERR, "redlens token helper: {$message}" . PHP_EOL);
    exit(1);
}
