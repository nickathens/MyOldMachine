/*
 * The program launchd starts for the bot on macOS, in front of Python.
 *
 * macOS gives a privacy grant (files on a removable volume, Accessibility)
 * to the program responsible for the request.  For a launchd job that is the
 * first program the job runs that is not part of macOS, and every process the
 * job starts inherits it.  Without this starter that program is Homebrew's
 * Python, whose signature changes with every Python update, so each update
 * dropped the grants and the next drive access waited on a consent box that
 * nobody was there to answer.  This program does not change, so a grant
 * given to it survives every update.
 *
 * It starts the command as its child and keeps running until the command
 * ends, because the child inherits this program as the one responsible for
 * whatever it asks for.  It passes on the signals launchd and the restart
 * command send, and exits the way the command exited.  The command stays in
 * this process group, so when launchd ends the job nothing outlives it.
 *
 * A built copy is never rebuilt.  Its ad hoc signature is a hash of its
 * bytes, so a rebuild is a new program to macOS and every grant would have
 * to be given again.  Editing this file therefore changes nothing on a
 * machine that already has a starter.  See install/macos_starter.py.
 */

#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>

extern char **environ;

static const int forwarded[] = {SIGHUP, SIGINT, SIGQUIT, SIGTERM, SIGUSR1, SIGUSR2};
#define NFORWARDED ((int)(sizeof forwarded / sizeof forwarded[0]))

static volatile sig_atomic_t child;

static void forward(int sig)
{
    if (child > 0)
        kill(child, sig);
}

int main(int argc, char *argv[])
{
    sigset_t held, none;
    struct sigaction sa;
    posix_spawnattr_t attr;
    siginfo_t info;
    pid_t pid;
    int i, err, status;

    if (argc < 2) {
        fprintf(stderr, "usage: %s program [argument ...]\n", argv[0]);
        return 64;
    }

    /* Hold the signals until the child exists, so none is lost in between. */
    sigemptyset(&held);
    for (i = 0; i < NFORWARDED; i++)
        sigaddset(&held, forwarded[i]);
    sigprocmask(SIG_BLOCK, &held, NULL);

    memset(&sa, 0, sizeof sa);
    sa.sa_handler = forward;
    sigemptyset(&sa.sa_mask);
    for (i = 0; i < NFORWARDED; i++)
        sigaction(forwarded[i], &sa, NULL);

    /* The child starts with nothing held and the default handlers. */
    sigemptyset(&none);
    posix_spawnattr_init(&attr);
    posix_spawnattr_setsigmask(&attr, &none);
    posix_spawnattr_setsigdefault(&attr, &held);
    posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF);
    err = posix_spawn(&pid, argv[1], NULL, &attr, argv + 1, environ);
    posix_spawnattr_destroy(&attr);
    if (err != 0) {
        fprintf(stderr, "%s: cannot start %s: %s\n", argv[0], argv[1], strerror(err));
        return 127;
    }
    child = pid;
    sigprocmask(SIG_UNBLOCK, &held, NULL);

    /*
     * Wait without reaping, stop forwarding, then reap.  Until it is reaped
     * the pid cannot be handed to another process, so a late signal can
     * never reach one.
     */
    while (waitid(P_PID, (id_t)pid, &info, WEXITED | WNOWAIT) < 0) {
        if (errno != EINTR) {
            perror("waitid");
            return 1;
        }
    }
    child = 0;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno != EINTR) {
            perror("waitpid");
            return 1;
        }
    }
    if (WIFEXITED(status))
        return WEXITSTATUS(status);
    if (WIFSIGNALED(status))
        return 128 + WTERMSIG(status);
    return 1;
}
