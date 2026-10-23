import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UserRole, type User } from "@/types";

import UsersTab from "./UsersTab";

const getUsers = vi.fn();
const updateUser = vi.fn();
const updateUserRole = vi.fn();
const deleteUser = vi.fn();

vi.mock("@/lib/api", () => ({
  getUsers: (...args: unknown[]) => getUsers(...args),
  createUser: vi.fn(),
  deleteUser: (...args: unknown[]) => deleteUser(...args),
  updateUser: (...args: unknown[]) => updateUser(...args),
  updateUserRole: (...args: unknown[]) => updateUserRole(...args),
}));

const addNotification = vi.fn();

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

const context = {
  userId: 1,
  isSuperuser: false,
  role: UserRole.ADMIN as UserRole | null,
};

vi.mock("./SettingsProvider", () => ({
  useSettingsContext: () => context,
}));

const account = (id: number, username: string, role: UserRole): User => ({
  id,
  username,
  role,
  is_active: true,
  is_superuser: false,
  force_password_change: false,
});

const OWNER = account(2, "alice.example", UserRole.OWNER);
const MEMBER = account(3, "bob.example", UserRole.USER);

async function renderAs(viewer: { isSuperuser: boolean; role: UserRole }) {
  context.isSuperuser = viewer.isSuperuser;
  context.role = viewer.role;
  getUsers.mockResolvedValue({
    items: [account(1, "me.example", viewer.role), OWNER, MEMBER],
    total: 3,
  });
  render(<UsersTab />);
  await screen.findByText("bob.example");
}

const LAST_OWNER =
  "Nojoin needs at least one active Owner. Make another account an Owner " +
  "before changing or removing this one.";

/** The API's refusal, as axios rejects with it. */
const refusal = (detail: string) => ({
  isAxiosError: true,
  response: { status: 409, data: { detail } },
});

const roleOptions = (select: HTMLElement) =>
  within(select)
    .getAllByRole("option")
    .map((option) => option.textContent);

describe("UsersTab", () => {
  beforeEach(() => {
    getUsers.mockReset();
    updateUser.mockReset().mockResolvedValue(MEMBER);
    updateUserRole.mockReset().mockResolvedValue(MEMBER);
    deleteUser.mockReset();
    addNotification.mockReset();
  });

  it("gives the setup account the full edit dialog", async () => {
    await renderAs({ isSuperuser: true, role: UserRole.OWNER });

    fireEvent.click(screen.getByRole("button", { name: "Edit bob.example" }));

    expect(await screen.findByLabelText("Username")).toBeInTheDocument();
    expect(screen.getByLabelText("Active account")).toBeInTheDocument();
  });

  it("sends the setup account's edit without an unchanged role", async () => {
    // The API refuses a role on an Owner's account from anyone but an Owner,
    // so a superuser who is not one could otherwise never save it.
    await renderAs({ isSuperuser: true, role: UserRole.ADMIN });

    fireEvent.click(screen.getByRole("button", { name: "Edit alice.example" }));
    const role = await screen.findByLabelText("Role");
    expect(role).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(updateUser).toHaveBeenCalledTimes(1));
    expect(updateUser.mock.calls[0][1]).not.toHaveProperty("role");
  });

  it("offers any other administrator a role change and nothing else", async () => {
    await renderAs({ isSuperuser: false, role: UserRole.ADMIN });

    expect(
      screen.queryByRole("button", { name: /^Edit / }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Change the role of bob.example" }),
    );
    const role = await screen.findByLabelText("Role");
    expect(screen.queryByLabelText("Username")).not.toBeInTheDocument();

    fireEvent.change(role, { target: { value: UserRole.ADMIN } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(updateUserRole).toHaveBeenCalledWith(MEMBER.id, UserRole.ADMIN),
    );
    expect(updateUser).not.toHaveBeenCalled();
  });

  it("leaves an Owner's account alone for an Admin", async () => {
    await renderAs({ isSuperuser: false, role: UserRole.ADMIN });

    expect(
      screen.queryByRole("button", { name: "Change the role of alice.example" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Delete alice.example" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Delete bob.example" }),
    ).toBeInTheDocument();
  });

  it("offers the Owner role only to an Owner", async () => {
    await renderAs({ isSuperuser: false, role: UserRole.ADMIN });
    fireEvent.click(screen.getByRole("button", { name: "Add user" }));
    expect(roleOptions(screen.getByLabelText("Role"))).toEqual(["User", "Admin"]);
  });

  it("lets an Owner change another Owner and grant the role", async () => {
    await renderAs({ isSuperuser: false, role: UserRole.OWNER });

    fireEvent.click(screen.getByRole("button", { name: "Add user" }));
    expect(roleOptions(screen.getByLabelText("Role"))).toEqual([
      "User",
      "Admin",
      "Owner",
    ]);
    expect(
      screen.getByRole("button", { name: "Change the role of alice.example" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Delete alice.example" }),
    ).toBeInTheDocument();
  });

  it("shows the API's refusal when the last Owner would step down", async () => {
    await renderAs({ isSuperuser: false, role: UserRole.OWNER });
    updateUserRole.mockRejectedValue(refusal(LAST_OWNER));

    fireEvent.click(
      screen.getByRole("button", { name: "Change the role of me.example" }),
    );
    fireEvent.change(await screen.findByLabelText("Role"), {
      target: { value: UserRole.ADMIN },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(addNotification).toHaveBeenCalledWith({
        message: LAST_OWNER,
        type: "error",
      }),
    );
    // The dialog stays open on the refused change, to pick another.
    expect(screen.getByLabelText("Role")).toHaveValue(UserRole.ADMIN);
  });

  it("shows the API's refusal of the setup account's edit", async () => {
    await renderAs({ isSuperuser: true, role: UserRole.OWNER });
    updateUser.mockRejectedValue(refusal(LAST_OWNER));

    fireEvent.click(screen.getByRole("button", { name: "Edit me.example" }));
    fireEvent.click(await screen.findByLabelText("Active account"));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() =>
      expect(addNotification).toHaveBeenCalledWith({
        message: LAST_OWNER,
        type: "error",
      }),
    );
    expect(updateUser.mock.calls[0][1]).toMatchObject({ is_active: false });
  });

  it("shows the API's refusal of a delete", async () => {
    // As when the viewer lost the Owner role to a change made meanwhile.
    await renderAs({ isSuperuser: false, role: UserRole.OWNER });
    deleteUser.mockRejectedValue(refusal(LAST_OWNER));

    fireEvent.click(screen.getByRole("button", { name: "Delete alice.example" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(addNotification).toHaveBeenCalledWith({
        message: LAST_OWNER,
        type: "error",
      }),
    );
  });
});
