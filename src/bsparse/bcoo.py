from numbers import Integral
from warnings import warn

import numpy as np
import scipy.sparse as sp
from numpy.typing import ArrayLike

from bsparse import sparse
from bsparse.bsparse import BSparse


class BCOO(BSparse):
    """A sparse matrix container in COOrdinate format.

    The ``BCOO`` class represents a sparse matrix using three arrays:

    * ``rows``: contains the row coordinates of the non-zero elements.
    * ``cols``: contains the column coordinates of the non-zero elements.
    * ``data``: contains the values of the non-zero elements.

    Upon creation, the matrix is sorted (lexicographically) by rows and
    columns. Duplicate elements are not allowed.

    Parameters
    ----------
    rows : array_like
        The row coordinates of the non-zero elements.
    cols : array_like
        The column coordinates of the non-zero elements.
    data : array_like
        The values of the non-zero constituents. This is an array of
        dtype object. Each element of the array can be either a 2D dense
        array, a scipy sparse matrix, or any bsparse matrix.
    bshape : tuple, optional
        The shape of the matrix container. If not given, it is inferred
        from ``rows`` and ``cols``.
    dtype : dtype, optional
        The data type of the matrix elements. If not given, it is
        inferred from ``data``.
    symmetry : str, optional
        The symmetry of the matrix. If not given, no symmetry is
        assumed. This is only applicable for square matrices, where
        possible values are ``'symmetric'`` and ``'hermitian'``. Note
        that when setting a symmetry, the lower triangular part of the
        matrix is discarded.

    """

    def __init__(
        self,
        rows: ArrayLike,
        cols: ArrayLike,
        data: ArrayLike,
        bshape: tuple[int, int] | None = None,
        dtype: np.dtype | None = None,
        symmetry: str | None = None,
    ) -> None:
        """Initializes a ``BCOO`` matrix."""
        self.rows = np.asarray(rows, dtype=int)
        self.cols = np.asarray(cols, dtype=int)

        data = list(data)
        self.data = self._validate_data(data)

        if len(self.data) != len(set(zip(self.rows, self.cols))):
            raise ValueError("Matrix has duplicate elements.")

        if dtype is None and len(self.data) != 0:
            dtype = np.result_type(*[b.dtype for b in self.data])
        if dtype is None:
            dtype = np.dtype(float)
        self.data = [b.astype(dtype) for b in self.data]
        self._dtype = dtype

        self._bshape = self._validate_bshape(bshape)
        self._symmetry = self._validate_symmetry(symmetry)
        self._sort_indices()

        self._row_sizes = self.row_sizes
        self._col_sizes = self.col_sizes

    def _validate_data(self, data: ArrayLike) -> list:
        """Validates the data blocks of the matrix."""
        # Check that the matrix blocks allow an equivalent array representation.
        for b in data:
            if b.ndim != 2:
                raise ValueError("Matrix blocks must be two dimensional.")
            if not hasattr(b, "shape"):
                raise ValueError("Matrix blocks must have a `shape` attribute.")
            if not hasattr(b, "dtype"):
                raise ValueError("Matrix blocks must have a `dtype` attribute.")
            if not hasattr(b, "astype"):
                raise ValueError("Matrix blocks must implement an `astype` method.")

        # Check that the matrix rows are aligned.
        for row in self.rows:
            row_data = [b for b, r in zip(data, self.rows) if r == row]
            if len(set([b.shape[0] for b in row_data])) != 1:
                raise ValueError("Matrix rows are not aligned.")

        # Check that the matrix columns are aligned.
        for col in self.cols:
            col_data = [b for b, c in zip(data, self.cols) if c == col]
            if len(set([b.shape[1] for b in col_data])) != 1:
                raise ValueError("Matrix columns are not aligned.")

        return data

    def _validate_bshape(self, bshape: tuple[int, int] | None) -> tuple[int, int]:
        """Validates the bshape of the matrix."""
        if bshape is None:
            if len(self.data) == 0:
                raise ValueError("Cannot instantiate empty matrix without bshape.")
            return (self.rows.max() + 1, self.cols.max() + 1)
        if len(self.data) != 0:  # Allows empty matrices.
            if self.rows.max() >= bshape[0] or self.cols.max() >= bshape[1]:
                raise ValueError("Matrix has out-of-bounds indices.")
        if bshape[0] < 0 or bshape[1] < 0:
            raise ValueError("Matrix has negative bshape.")
        return bshape

    def _validate_symmetry(self, symmetry: str | None) -> str | None:
        """Validates the symmetry of the matrix."""
        if symmetry is None:
            return symmetry
        if symmetry not in ("symmetric", "hermitian"):
            raise ValueError("Invalid symmetry.")
        if self.bshape[0] != self.bshape[1]:
            raise ValueError("Symmetry is only applicable to square matrices.")

        self._discard_subdiagonal()

        return symmetry

    def _discard_subdiagonal(self) -> None:
        """Takes symmetry into account by removing the lower triangular part."""
        if any(self.rows > self.cols):
            warn(
                "Symmetric matrix is not upper triangular. "
                "Lower triangular part is discarded."
            )
            mask = self.rows <= self.cols
            self.rows = self.rows[mask]
            self.cols = self.cols[mask]
            self.data = [b for b, m in zip(self.data, mask) if m]

    def _sort_indices(self) -> None:
        """Sorts the matrix by rows and columns."""
        order = np.lexsort((self.cols, self.rows))
        self.rows = self.rows[order]
        self.cols = self.cols[order]
        self.data = [self.data[i] for i in order]

    def _unsign_index(self, row: int, col: int) -> tuple:
        """Adjusts the sign to allow negative indices and checks bounds."""
        row = self.bshape[0] + row if row < 0 else row
        col = self.bshape[1] + col if col < 0 else col
        if not (0 <= row < self.bshape[0] and 0 <= col < self.bshape[1]):
            raise IndexError("Block index out of bounds.")

        return row, col

    def _getitem_symmetry(self, row: int | slice, col: int | slice):
        """Returns the element at the given coordinates."""
        if isinstance(row, Integral) and isinstance(col, Integral):
            row, col = self._unsign_index(row, col)
            if row <= col:
                ind = np.nonzero((self.rows == row) & (self.cols == col))[0]
                if ind.size == 0:
                    return sparse.zeros(
                        (self.row_sizes[row], self.col_sizes[col]), self.dtype
                    )
                return self.data[ind[0]]

            if self.symmetry == "symmetric":
                return self[col, row]
            if self.symmetry == "hermitian":
                return self[col, row].conjugate()

        if isinstance(row, Integral):
            row = slice(row, row + 1)
        if isinstance(col, Integral):
            col = slice(col, col + 1)

        rows = np.arange(*row.indices(self.bshape[0]))
        cols = np.arange(*col.indices(self.bshape[1]))
        if len(rows) == 0 or len(cols) == 0:
            raise IndexError("Slice index out of bounds.")
        rows = np.array([self._unsign_index(r, 0)[0] for r in rows])
        cols = np.array([self._unsign_index(0, c)[1] for c in cols])

        row_step = row.step if row.step is not None else 1
        col_step = col.step if col.step is not None else 1

        if row.start == col.start and row.stop == col.stop and row_step == col_step:
            # If the slice is symmetric, we need to return a symmetric matrix.
            mask = np.isin(self.rows, rows) & np.isin(self.cols, cols)
            submatrix = BCOO(
                (self.rows[mask] - rows[0]) // row_step,
                (self.cols[mask] - cols[0]) // col_step,
                [b for b, m in zip(self.data, mask) if m],
                bshape=(len(rows), len(cols)),
                dtype=self.dtype,
                symmetry=self.symmetry,
            )
            return submatrix

        submatrix = BCOO([], [], [], bshape=(len(rows), len(cols)), dtype=self.dtype)
        for i, j in np.ndindex(submatrix.bshape):
            value = self[int(rows[i]), int(cols[j])]
            if isinstance(value, (sparse.Sparse, sp.spmatrix)) and value.nnz == 0:
                continue
            if isinstance(value, np.ndarray) and np.all(value == 0):
                continue
            submatrix.rows = np.append(submatrix.rows, (rows[i] - rows[0]) // row_step)
            submatrix.cols = np.append(submatrix.cols, (cols[j] - cols[0]) // col_step)
            submatrix.data.append(value)
        return submatrix

    def _getslice(self, row: slice, col: slice):
        """Returns a submatrix."""
        rows = np.arange(*row.indices(self.bshape[0]))
        cols = np.arange(*col.indices(self.bshape[1]))
        if len(rows) == 0 or len(cols) == 0:
            raise IndexError("Slice index out of bounds.")
        rows = np.array([self._unsign_index(r, 0)[0] for r in rows])
        cols = np.array([self._unsign_index(0, c)[1] for c in cols])
        row_step = row.step if row.step is not None else 1
        col_step = col.step if col.step is not None else 1
        mask = np.isin(self.rows, rows) & np.isin(self.cols, cols)
        submatrix = BCOO(
            (self.rows[mask] - rows[0]) // row_step,
            (self.cols[mask] - cols[0]) // col_step,
            [b for b, m in zip(self.data, mask) if m],
            bshape=(len(rows), len(cols)),
            dtype=self.dtype,
        )
        return submatrix

    def __getitem__(
        self, key: int | slice | tuple
    ) -> "np.ndarray | sparse.Sparse | BSparse":
        """Returns a matrix element or a submatrix."""
        if isinstance(key, (Integral, slice)):
            key = (key, slice(None))

        if not isinstance(key, tuple) or len(key) != 2:
            raise IndexError("Invalid index")

        row, col = key
        if not isinstance(row, (Integral, slice)) or not isinstance(
            col, (Integral, slice)
        ):
            raise IndexError("Invalid index")

        if self.symmetry is not None:
            return self._getitem_symmetry(row, col)

        if isinstance(row, Integral) and isinstance(col, Integral):
            row, col = self._unsign_index(row, col)
            ind = np.nonzero((self.rows == row) & (self.cols == col))[0]
            if ind.size == 0:
                return sparse.zeros(
                    (self.row_sizes[row], self.col_sizes[col]), self.dtype
                )
            return self.data[ind[0]]

        if isinstance(row, Integral):
            row = slice(row, row + 1)
        if isinstance(col, Integral):
            col = slice(col, col + 1)

        return self._getslice(row, col)

    def __setitem__(
        self,
        key: tuple[int, int],
        value: "np.ndarray | sparse.Sparse | BSparse",
    ):
        """Sets a matrix element or a submatrix."""
        if not isinstance(key, tuple) or len(key) != 2:
            raise IndexError("Invalid index.")

        if not isinstance(value, (np.ndarray, sparse.Sparse, BSparse)):
            raise ValueError("Invalid value.")

        value = value.astype(self.dtype)

        row, col = key
        if not isinstance(row, int) or not isinstance(col, int):
            raise IndexError("Invalid index")

        row, col = self._unsign_index(row, col)
        if self.symmetry is not None and row > col:
            if self.symmetry == "symmetric":
                self[col, row] = value
                return
            if self.symmetry == "hermitian":
                self[col, row] = value.conjugate()
                return

        all_zero = (
            isinstance(value, (sparse.Sparse, sp.spmatrix))
            and value.nnz == 0
            or isinstance(value, np.ndarray)
            and np.all(value == 0)
        )

        mask = (self.rows == row) & (self.cols == col)
        if any(mask):
            if all_zero:
                mask = ~mask
                self.rows = self.rows[mask]
                self.cols = self.cols[mask]
                self.data = [b for b, m in zip(self.data, mask) if m]
                self._row_sizes[row] = 1
                self._col_sizes[col] = 1
                return
            ind = np.nonzero(mask)[0][0]
            self.data[ind] = value
            self._row_sizes[row] = value.shape[0]
            self._col_sizes[col] = value.shape[1]
            return

        if all_zero:
            return

        self.rows = np.append(self.rows, row)
        self.cols = np.append(self.cols, col)
        self.data = self._validate_data(self.data + [value])
        self._sort_indices()
        self._row_sizes[row] = value.shape[0]
        self._col_sizes[col] = value.shape[1]

    def __add__(self, other: "np.number | BSparse") -> "BCOO":
        """Adds another matrix or a scalar to this matrix."""
        ...

    def __sub__(self, other: "np.number | BSparse") -> "BCOO":
        """Subtracts another matrix or a scalar from this matrix."""
        ...

    def __rsub__(self, other: "np.number | BSparse") -> "BCOO":
        """Subtracts this matrix from another matrix or a scalar."""
        ...

    def __mul__(self, other: "np.number | BSparse") -> "BCOO":
        """Multiplies another matrix or a scalar by this matrix."""
        ...

    def __truediv__(self, other: "np.number | BSparse") -> "BCOO":
        """Divides this matrix by another matrix or a scalar."""
        ...

    def __rtruediv__(self, other: "np.number | BSparse") -> "BCOO":
        """Divides another matrix or a scalar by this matrix."""
        ...

    def __neg__(self) -> "BCOO":
        """Negates this matrix."""
        ...

    def __matmul__(self, other: "BSparse") -> "BCOO":
        """Multiplies this matrix by another matrix."""
        ...

    def __rmatmul__(self, other: "BSparse") -> "BCOO":
        """Multiplies another matrix by this matrix."""
        ...

    @property
    def bshape(self) -> tuple[int, int]:
        """The block shape of the matrix."""
        return self._bshape

    @property
    def row_sizes(self) -> np.ndarray:
        """The sizes of the row elements."""
        if hasattr(self, "_row_sizes"):
            return self._row_sizes

        sizes = np.zeros(self.bshape[0], dtype=int)
        for row in range(self.bshape[0]):
            row_data = [b for b, r in zip(self.data, self.rows) if r == row]
            if len(row_data) == 0:
                sizes[row] = 1
                continue
            sizes[row] = row_data[0].shape[0]

        return sizes

    @property
    def col_sizes(self) -> np.ndarray:
        """The sizes of the column elements."""
        if hasattr(self, "_col_sizes"):
            return self._col_sizes

        sizes = np.zeros(self.bshape[1], dtype=int)
        for col in range(self.bshape[1]):
            col_data = [b for b, c in zip(self.data, self.cols) if c == col]
            if len(col_data) == 0:
                sizes[col] = 1
                continue
            sizes[col] = col_data[0].shape[1]

        return sizes

    @property
    def dtype(self) -> np.dtype:
        """The data type of the matrix elements."""
        return self._dtype

    @property
    def bnnz(self) -> int:
        """The number of non-zero elements in the matrix."""
        return len(self.data)

    @property
    def nnz(self) -> int:
        """The number of non-zero elements in the matrix."""
        return sum(b.size if hasattr(b, "size") else b.nnz for b in self.data)

    @property
    def symmetry(self) -> str | None:
        """The symmetry of the matrix."""
        return self._symmetry

    @property
    def T(self) -> "BCOO":
        """The transpose of the matrix."""
        if self.symmetry == "symmetric":
            return self
        if self.symmetry == "hermitian":
            return self.conjugate()
        transpose = BCOO(
            self.cols,
            self.rows,
            self.data,
            (self.bshape[1], self.bshape[0]),
            self.dtype,
            self.symmetry,
        )
        return transpose

    @property
    def H(self) -> "BCOO":
        """The conjugate transpose of the matrix."""
        if self.symmetry == "hermitian":
            return self
        if self.symmetry == "symmetric":
            return self.conjugate()
        hermitian = BCOO(
            self.cols,
            self.rows,
            [b.conjugate() for b in self.data],
            (self.bshape[1], self.bshape[0]),
            self.dtype,
            self.symmetry,
        )
        return hermitian

    def conjugate(self) -> "BCOO":
        """The complex conjugate of the matrix."""
        conjugate = BCOO(
            self.rows,
            self.cols,
            [b.conjugate() for b in self.data],
            self.bshape,
            self.dtype,
            self.symmetry,
        )
        return conjugate

    def diagonal(self, offset: int = 0) -> np.ndarray:
        """Returns the block diagonal of the matrix."""
        if not -self.bshape[0] < offset < self.bshape[1]:
            raise ValueError("Offset out of bounds.")

        if offset < 0:
            if self.symmetry == "hermitian":
                return [b.conjugate().T for b in self.diagonal(-offset)]
            if self.symmetry == "symmetric":
                return [b.T for b in self.diagonal(-offset)]

        start = (-offset) * self.bshape[1] if offset < 0 else offset

        rows = []
        cols = []
        for flat_ind in range(
            start, self.bshape[0] * self.bshape[1], self.bshape[1] + 1
        ):
            if flat_ind // self.bshape[1] >= self.bshape[1] - offset:
                break
            rows.append(flat_ind // self.bshape[1])
            cols.append(flat_ind % self.bshape[1])

        diag = []
        for row, col in zip(rows, cols):
            ind = np.nonzero((self.rows == row) & (self.cols == col))[0]
            if ind.size == 0:
                diag.append(
                    sparse.zeros((self.row_sizes[row], self.col_sizes[col]), self.dtype)
                )
                continue
            diag.append(self.data[ind[0]])
        return diag

    def copy(self) -> "BCOO":
        """Returns a copy of the matrix."""
        return BCOO(
            self.rows.copy(),
            self.cols.copy(),
            self.data.copy(),
            self.bshape,
            self.dtype,
            self.symmetry,
        )

    def astype(self, dtype: np.dtype) -> "BCOO":
        """Returns a copy of the matrix with a different data type."""
        new = BCOO(
            self.rows.copy(),
            self.cols.copy(),
            self.data.copy(),
            self.bshape,
            dtype,
            self.symmetry,
        )
        return new

    def toarray(self) -> np.ndarray:
        """Converts the matrix to a dense `numpy.ndarray`."""
        arr = np.zeros(self.shape, dtype=self.dtype)
        row_offsets = np.cumsum(self.row_sizes) - self.row_sizes
        col_offsets = np.cumsum(self.col_sizes) - self.col_sizes

        for row, col, b in zip(self.rows, self.cols, self.data):
            arr[
                row_offsets[row] : row_offsets[row] + b.shape[0],
                col_offsets[col] : col_offsets[col] + b.shape[1],
            ] = (
                b if isinstance(b, np.ndarray) else b.toarray()
            )

        if self.symmetry == "symmetric":
            temp = arr.T.copy()
            temp[np.nonzero(arr)] = 0
            arr += temp
        if self.symmetry == "hermitian":
            temp = arr.conj().T.copy()
            temp[np.nonzero(arr)] = 0
            arr += temp

        return arr

    def tocoo(self) -> "BCOO":
        """Converts the matrix to `BCOO` format."""
        return self

    def tocsr(self) -> "BSparse":
        """Converts the matrix to `BCSR` format."""
        from bsparse.bcsr import BCSR

        self._sort_indices()
        rowptr = np.zeros(self.bshape[0] + 1, dtype=int)
        for row in self.rows:
            rowptr[row + 1] += 1
        rowptr = np.cumsum(rowptr)

        csr = BCSR(
            rowptr,
            self.cols,
            self.data,
            self.bshape,
            self.dtype,
            self.symmetry,
        )
        return csr

    def todia(self) -> "BSparse":
        """Converts the matrix to `BDIA` format."""
        offsets = []
        data = []
        start = -self.bshape[0] + 1 if self.symmetry is None else 0
        for offset in range(start, self.bshape[1]):
            bdiag = self.diagonal(offset)
            if all(
                [
                    np.all(b == 0) if isinstance(b, np.ndarray) else b.nnz == 0
                    for b in bdiag
                ]
            ):
                continue
            offsets.append(offset)
            data.append(bdiag)

        from bsparse.bdia import BDIA

        if len(offsets) == 0:
            return BDIA([], [[]], self.bshape, self.dtype, self.symmetry)
        return BDIA(offsets, data, self.bshape, self.dtype, self.symmetry)

    def save_npz(self, filename: str) -> None:
        """Saves the matrix to a `numpy.npz` file."""
        np.savez_compressed(
            filename,
            format="bcoo",
            rows=self.rows,
            cols=self.cols,
            data=self.data,
            bshape=self.bshape,
            dtype=self.dtype,
            symmetry=self.symmetry,
        )

    @classmethod
    def from_array(
        cls,
        arr: np.ndarray,
        sizes: tuple[ArrayLike, ArrayLike],
        symmetry: str | None = None,
    ) -> "BCOO":
        """Creates a `BCOO` matrix from a dense `numpy.ndarray`."""
        arr = np.asarray(arr)
        row_sizes, col_sizes = sizes
        if arr.shape != (np.sum(row_sizes), np.sum(col_sizes)):
            raise ValueError("Array shape does not match block sizes.")
        row_offsets = np.cumsum(row_sizes) - row_sizes
        col_offsets = np.cumsum(col_sizes) - col_sizes

        rows = []
        cols = []
        data = []
        for i, rr in enumerate(row_offsets):
            for j, cc in enumerate(col_offsets):
                b = arr[rr : rr + row_sizes[i], cc : cc + col_sizes[j]]
                if np.all(b == 0):
                    continue
                rows.append(i)
                cols.append(j)
                data.append(b)

        return cls(
            rows, cols, data, (len(row_sizes), len(col_sizes)), symmetry=symmetry
        )

    @classmethod
    def from_spmatrix(
        cls,
        mat: sp.spmatrix,
        sizes: tuple[ArrayLike, ArrayLike],
        symmetry: str | None = None,
    ) -> "BCOO":
        """Creates a `BCOO` matrix from a `scipy.sparse.spmatrix`."""
        mat = sp.lil_array(mat)
        row_sizes, col_sizes = sizes
        if mat.shape != (np.sum(row_sizes), np.sum(col_sizes)):
            raise ValueError("Matrix shape does not match block sizes.")
        row_offsets = np.cumsum(row_sizes) - row_sizes
        col_offsets = np.cumsum(col_sizes) - col_sizes

        rows = []
        cols = []
        data = []
        for i, rr in enumerate(row_offsets):
            for j, cc in enumerate(col_offsets):
                b = mat[rr : rr + row_sizes[i], cc : cc + col_sizes[j]]
                if b.nnz == 0:
                    continue
                rows.append(i)
                cols.append(j)
                data.append(b)

        return cls(
            rows, cols, data, (len(row_sizes), len(col_sizes)), symmetry=symmetry
        )