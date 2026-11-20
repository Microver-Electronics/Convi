procedure Body;
var D, S, It, C, PIt, P, W: Variant; i: Integer; Loc: TLocation;
begin
  D := Client.OpenDocument('SCH', '%OUT%\e2e_ecc83\ecc83-pp.SchDoc');
  Client.ShowDocument(D);
  S := SchServer.GetSchDocumentByPath('%OUT%\e2e_ecc83\ecc83-pp.SchDoc');
  It := S.SchIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eSchComponent));
  C := It.FirstSchObject;
  while C <> Nil do
  begin
    if (C.Designator.Text = 'R2') or (C.Designator.Text = 'C1') then
    begin
      L(C.Designator.Text + ' loc=' + IntToStr(CoordToMils(C.Location.X)) + ',' + IntToStr(CoordToMils(C.Location.Y)) + ' o=' + IntToStr(C.Orientation) + ' mir=' + BoolToStr(C.IsMirrored, True) + ' part=' + IntToStr(C.CurrentPartID) + ' mode=' + IntToStr(C.DisplayMode));
      PIt := C.SchIterator_Create;
      PIt.AddFilter_ObjectSet(MkSet(ePin));
      P := PIt.FirstSchObject;
      while P <> Nil do
      begin
        L('  pin ' + P.Designator + ' loc=' + FloatToStr(CoordToMils(P.Location.X)) + ',' + FloatToStr(CoordToMils(P.Location.Y)) + ' o=' + IntToStr(P.Orientation) + ' len=' + IntToStr(CoordToMils(P.PinLength)) + ' owner=' + IntToStr(P.OwnerPartId) + ' mode=' + IntToStr(P.OwnerPartDisplayMode));
        P := PIt.NextSchObject;
      end;
      C.SchIterator_Destroy(PIt);
    end;
    C := It.NextSchObject;
  end;
  S.SchIterator_Destroy(It);
  It := S.SchIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePowerObject));
  W := It.FirstSchObject;
  while W <> Nil do
  begin
    L('power ' + W.Text + ' loc=' + FloatToStr(CoordToMils(W.Location.X)) + ',' + FloatToStr(CoordToMils(W.Location.Y)) + ' o=' + IntToStr(W.Orientation));
    W := It.NextSchObject;
  end;
  S.SchIterator_Destroy(It);
  It := S.SchIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eWire));
  W := It.FirstSchObject;
  i := 0;
  while (W <> Nil) and (i < 40) do
  begin
    L('wire n=' + IntToStr(W.VerticesCount) + ' v1=' + FloatToStr(CoordToMils(W.Vertex[1].X)) + ',' + FloatToStr(CoordToMils(W.Vertex[1].Y)) + ' v2=' + FloatToStr(CoordToMils(W.Vertex[2].X)) + ',' + FloatToStr(CoordToMils(W.Vertex[2].Y)));
    W := It.NextSchObject;
    i := i + 1;
  end;
  S.SchIterator_Destroy(It);
end;
