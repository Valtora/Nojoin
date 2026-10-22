import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  holdSignInInPlace,
  noteSessionRefusal,
  noteSignedIn,
  resetSessionForTests,
  useSessionStore,
} from "@/lib/session";
import { renderWithProviders } from "@/test/renderWithProviders";

import SessionSignInDialog, { SESSION_PROBE_INTERVAL_MS } from "./SessionSignInDialog";

const apiMocks = vi.hoisted(() => ({
  login: vi.fn(),
  getCurrentUser: vi.fn(),
}));

const captureState = vi.hoisted(() => ({
  status: "recording",
  runtimeActive: true,
  stop: vi.fn(),
  uploadQueue: { segments: 0, seconds: 0 },
}));

vi.mock("@/lib/api", () => ({
  login: apiMocks.login,
  getCurrentUser: apiMocks.getCurrentUser,
}));

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => captureState,
}));

const alice = { username: "alice", force_password_change: false };

const endSession = () => {
  act(() => {
    useSessionStore.setState({ signInRequired: true });
  });
};

const passwordField = () => screen.getByLabelText("Password");

describe("session sign-in dialog", () => {
  beforeEach(() => {
    resetSessionForTests();
    noteSignedIn(alice);
    holdSignInInPlace(true);
    captureState.status = "recording";
    captureState.runtimeActive = true;
    captureState.stop.mockReset();
    captureState.stop.mockResolvedValue({ id: "rec-1", status: "QUEUED" });
    captureState.uploadQueue = { segments: 0, seconds: 0 };
    apiMocks.login.mockReset();
    apiMocks.getCurrentUser.mockReset();
    apiMocks.getCurrentUser.mockRejectedValue(new Error("Not authenticated"));
  });

  afterEach(() => {
    vi.useRealTimers();
    resetSessionForTests();
  });

  it("stays shut while the session works", () => {
    renderWithProviders(<SessionSignInDialog />);

    expect(screen.queryByText("Sign in to keep uploading")).not.toBeInTheDocument();
  });

  it("stays shut when nothing holds sign-in in place", () => {
    holdSignInInPlace(false);
    renderWithProviders(<SessionSignInDialog />);
    endSession();

    expect(screen.queryByText("Sign in to keep uploading")).not.toBeInTheDocument();
  });

  it("asks the recording's own account to sign in, and says what is waiting", async () => {
    captureState.uploadQueue = { segments: 48, seconds: 95 };
    renderWithProviders(<SessionSignInDialog />);
    endSession();

    expect(await screen.findByText("Sign in to keep uploading")).toBeInTheDocument();
    expect(screen.getByText(/Nojoin is still recording/)).toBeInTheDocument();
    expect(screen.getByText("1 min 35 s of audio is waiting to upload.")).toBeInTheDocument();
    const username = screen.getByLabelText("Username");
    expect(username).toHaveValue("alice");
    expect(username).toHaveAttribute("readonly");
  });

  it("opens one dialog however many requests are refused", async () => {
    renderWithProviders(<SessionSignInDialog />);
    const refusal = Object.assign(new Error("Request failed with status code 401"), {
      response: { status: 401, data: { detail: "Not authenticated" } },
    });

    act(() => {
      noteSessionRefusal(refusal);
      noteSessionRefusal(refusal);
      noteSessionRefusal(refusal);
    });

    expect(await screen.findAllByText("Sign in to keep uploading")).toHaveLength(1);
    expect(screen.getAllByLabelText("Password")).toHaveLength(1);
  });

  it("stops the recording without waiting for the sign-in", async () => {
    renderWithProviders(<SessionSignInDialog />);
    endSession();

    fireEvent.click(await screen.findByRole("button", { name: "Stop recording" }));

    expect(captureState.stop).toHaveBeenCalledTimes(1);
    expect(apiMocks.login).not.toHaveBeenCalled();
  });

  it("offers no stop once the recording is stopping", async () => {
    captureState.status = "finalizing";
    renderWithProviders(<SessionSignInDialog />);
    endSession();

    expect(await screen.findByText("Sign in to keep uploading")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop recording" })).not.toBeInTheDocument();
  });

  it("names the other account the browser is signed in as", async () => {
    renderWithProviders(<SessionSignInDialog />);

    act(() => {
      noteSignedIn({ username: "bob", force_password_change: false });
    });

    expect(await screen.findByText(/This browser is now signed in as bob/)).toBeInTheDocument();
    expect(screen.getByLabelText("Username")).toHaveValue("alice");
  });

  it("signs in through the session login and closes", async () => {
    apiMocks.login.mockImplementation(async (username: string) => {
      noteSignedIn({ username, force_password_change: false });
      return { username, force_password_change: false, is_superuser: false };
    });
    renderWithProviders(<SessionSignInDialog />);
    endSession();

    fireEvent.change(await screen.findByLabelText("Password"), {
      target: { value: "correct horse" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(apiMocks.login).toHaveBeenCalledWith("alice", "correct horse"));
    await waitFor(() =>
      expect(screen.queryByText("Sign in to keep uploading")).not.toBeInTheDocument(),
    );
    expect(useSessionStore.getState().signInRequired).toBe(false);
  });

  it("closes only once the server names the signed-in account as the recording's", async () => {
    noteSignedIn({ ...alice, id: 1 });
    apiMocks.login.mockImplementation(async (username: string) => {
      noteSignedIn({ username, force_password_change: false });
      return { username, force_password_change: false, is_superuser: false };
    });
    // A new account was given the name "alice" after hers was renamed.
    apiMocks.getCurrentUser.mockImplementation(async () => {
      const user = { username: "alice", force_password_change: false, id: 3 };
      noteSignedIn(user);
      return user;
    });
    renderWithProviders(<SessionSignInDialog />);
    endSession();

    fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "theirs" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(apiMocks.getCurrentUser).toHaveBeenCalled());
    expect(apiMocks.login.mock.invocationCallOrder[0]).toBeLessThan(
      apiMocks.getCurrentUser.mock.invocationCallOrder[0],
    );
    expect(await screen.findByText(/This browser is now signed in as alice/)).toBeInTheDocument();
    expect(useSessionStore.getState()).toMatchObject({ signInRequired: true, userId: 1 });
  });

  it("shows why a sign-in failed and stays open", async () => {
    apiMocks.login.mockRejectedValue(
      Object.assign(new Error("Request failed"), {
        response: { status: 400, data: { detail: "Incorrect username or password" } },
      }),
    );
    renderWithProviders(<SessionSignInDialog />);
    endSession();

    fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Incorrect username or password")).toBeInTheDocument();
    expect(useSessionStore.getState().signInRequired).toBe(true);
  });

  it("explains a reset password, which cannot resume uploads", async () => {
    apiMocks.login.mockResolvedValue({
      username: "alice",
      force_password_change: true,
      is_superuser: false,
    });
    renderWithProviders(<SessionSignInDialog />);
    endSession();

    fireEvent.change(await screen.findByLabelText("Password"), {
      target: { value: "temporary" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText(/Your password was reset/)).toBeInTheDocument();
    expect(passwordField()).toHaveValue("");
    expect(screen.getByText("Sign in to keep uploading")).toBeInTheDocument();
  });

  it("asks for sign-in to save the notes when only notes not saved yet hold it", async () => {
    holdSignInInPlace(false);
    holdSignInInPlace(true, "notes");
    captureState.status = "idle";
    captureState.runtimeActive = false;
    apiMocks.login.mockResolvedValue({
      username: "alice",
      force_password_change: true,
      is_superuser: false,
    });
    renderWithProviders(<SessionSignInDialog />);
    act(() => {
      noteSignedIn({ username: "bob", force_password_change: false });
    });

    expect(await screen.findByText("Sign in to save your notes")).toBeInTheDocument();
    expect(screen.getByText(/with notes on this page not saved yet/)).toBeInTheDocument();
    expect(screen.queryByText(/recording/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop recording" })).not.toBeInTheDocument();
    expect(
      screen.getByText(/The notes belong to alice, so they save only once alice signs in again/),
    ).toBeInTheDocument();
    expect(screen.getByText("The notes belong to this account, so sign in as it.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go to the sign-in page" })).toBeEnabled();

    fireEvent.change(passwordField(), { target: { value: "temporary" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText(/so your notes cannot save until/)).toBeInTheDocument();
  });

  it("says notes wait too when a capture and notes both hold sign-in", async () => {
    holdSignInInPlace(true, "notes");
    renderWithProviders(<SessionSignInDialog />);
    endSession();

    expect(await screen.findByText("Sign in to keep uploading")).toBeInTheDocument();
    expect(screen.getByText(/Nojoin is still recording/)).toBeInTheDocument();
    expect(screen.getByText("Notes not saved yet wait too, and save once you sign in.")).toBeInTheDocument();
    // Leaving would reload the tab and end the capture.
    expect(screen.queryByRole("button", { name: "Go to the sign-in page" })).not.toBeInTheDocument();
  });

  it("describes a stop that is waiting to finish", async () => {
    captureState.status = "finalizing";
    renderWithProviders(<SessionSignInDialog />);
    endSession();

    expect(await screen.findByText(/Recording has stopped/)).toBeInTheDocument();
  });

  it("notices a sign-in made in another tab", async () => {
    vi.useFakeTimers();
    renderWithProviders(<SessionSignInDialog />);
    endSession();

    await act(async () => {
      vi.advanceTimersByTime(SESSION_PROBE_INTERVAL_MS);
    });
    expect(apiMocks.getCurrentUser).toHaveBeenCalledTimes(1);

    apiMocks.getCurrentUser.mockImplementation(async () => {
      noteSignedIn(alice);
      return alice;
    });
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });

    expect(apiMocks.getCurrentUser).toHaveBeenCalledTimes(2);
    expect(useSessionStore.getState().signInRequired).toBe(false);
  });
});
