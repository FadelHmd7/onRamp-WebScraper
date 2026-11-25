import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { AdminOnly, AuthProvider } from "./auth";
import { fakeApi, HEALTH, renderAt, signedIn } from "./test-utils";

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("login", () => {
  it("sends a visitor to the login page and back after signing in", async () => {
    const api = fakeApi({
      "POST /auth/token": [200, { access_token: "t1", role: "viewer" }],
      "/auth/me": { username: "vic", role: "viewer" },
      "/health": HEALTH,
    });
    renderAt("/", <App />);
    await userEvent.type(await screen.findByLabelText("Username"), "vic");
    await userEvent.type(screen.getByLabelText("Password"), "secret");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByText("viewer")).toBeInTheDocument();
    expect(sessionStorage.getItem("onramp.token")).toBe("t1");
    expect(api.calls[0]).toMatchObject({ method: "POST", path: "/auth/token" });
  });

  it("says when the password is wrong", async () => {
    fakeApi({ "POST /auth/token": () => [401, { detail: "Wrong username or password" }] });
    renderAt("/login", <App />);
    await userEvent.type(screen.getByLabelText("Username"), "vic");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong username or password.");
    expect(sessionStorage.getItem("onramp.token")).toBeNull();
  });

  it("keeps the session across a reload and ends it on sign out", async () => {
    signedIn("admin");
    fakeApi({ "/auth/me": { username: "ada", role: "admin" }, "/health": HEALTH });
    renderAt("/", <App />);
    expect(await screen.findByText("ada")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(sessionStorage.getItem("onramp.token")).toBeNull();
  });

  it("ends the session when the API rejects the token", async () => {
    signedIn("viewer");
    fakeApi({ "/auth/me": () => [401, { detail: "Invalid or expired token" }], "/health": HEALTH });
    renderAt("/", <App />);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    await waitFor(() => expect(sessionStorage.getItem("onramp.token")).toBeNull());
  });
});

describe("AdminOnly", () => {
  it.each([
    ["admin", true],
    ["viewer", false],
  ] as const)("for a %s, renders admin actions: %s", async (role, shown) => {
    signedIn(role);
    fakeApi({ "/auth/me": { username: "x", role } });
    renderAt(
      "/",
      <AuthProvider>
        <p>page</p>
        <AdminOnly>
          <button type="button">Start crawl</button>
        </AdminOnly>
      </AuthProvider>,
    );
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
    await screen.findByText("page");
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Start crawl" }) !== null).toBe(shown),
    );
  });
});
