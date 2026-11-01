import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode, useEffect } from "react";
import { MemoryRouter, Outlet, Route, Routes, useNavigate } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useScrollToTop } from "./useScrollToTop";

let order: string[] = [];
let spy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  order = [];
  spy = vi.spyOn(window, "scrollTo").mockImplementation(() => {
    order.push("top");
  });
});

afterEach(() => {
  spy.mockRestore();
});

function Layout() {
  useScrollToTop();
  return <Outlet />;
}

function Start() {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate("/b")}>
        Go to b
      </button>
      <button type="button" onClick={() => navigate("/a?tab=debt")}>
        Debt tab
      </button>
      <button type="button" onClick={() => navigate("/c", { replace: true })}>
        Replace with c
      </button>
    </>
  );
}

/** A page whose own effect scrolls, like Travel's ?trip= scrollIntoView. */
function Page({ name }: { name: string }) {
  const navigate = useNavigate();
  useEffect(() => {
    order.push("page");
  }, []);
  return (
    <button type="button" onClick={() => navigate(-1)}>
      Back from {name}
    </button>
  );
}

function App() {
  return (
    <MemoryRouter initialEntries={["/a"]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/a" element={<Start />} />
          <Route path="/b" element={<Page name="b" />} />
          <Route path="/c" element={<Page name="c" />} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
}

describe("useScrollToTop", () => {
  it("leaves the place alone on the first mount, even in StrictMode", () => {
    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );
    expect(spy).not.toHaveBeenCalled();
  });

  it("keeps the place on a query change and goes to the top on a new page", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Debt tab" }));
    expect(spy).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Go to b" }));
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith(0, 0);
    // The reset runs before the new page's own scroll effect.
    expect(order).toEqual(["top", "page"]);
  });

  it("leaves Back to the browser", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Go to b" }));
    expect(spy).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Back from b" }));
    expect(await screen.findByRole("button", { name: "Go to b" })).toBeTruthy();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("goes to the top on a replace to a new page", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Replace with c" }));
    expect(await screen.findByRole("button", { name: "Back from c" })).toBeTruthy();
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
