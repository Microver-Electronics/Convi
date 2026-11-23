{ p67: symbol primitives that can be picked one by one (2026-11-23).
  Altium writes ISNOTACCESIBLE=T on every graphic of a placed or library symbol; Convi's symbols
  had none, so a click on a line moved that line alone. Altium's own KiCad importer calls
  ISch_Component.UpdatePrimitivesAccessibility. This probe builds the same symbol twice in a
  SchDoc and in a SchLib, once without and once with that call, and logs PinsMoveable.
  Check: the saved files carry ISNOTACCESIBLE=T only on the 'WITH' parts. }

function Pt(X, Y: Integer): TLocation;
begin
  Result := Point(MilsToCoord(X), MilsToCoord(Y));
end;

procedure AddPrims(C: Variant; X0, Y0: Integer);
var R, A, P, Lb, Pl: Variant;
begin
  R := SchServer.SchObjectFactory(eRectangle, eCreate_Default);
  R.Location := Pt(X0, Y0); R.Corner := Pt(X0 + 400, Y0 + 200);
  R.OwnerPartId := 1; R.OwnerPartDisplayMode := 0;
  C.AddSchObject(R);
  A := SchServer.SchObjectFactory(eArc, eCreate_Default);
  A.Location := Pt(X0 + 200, Y0 + 300); A.Radius := MilsToCoord(50);
  A.StartAngle := 0; A.EndAngle := 180;
  A.OwnerPartId := 1; A.OwnerPartDisplayMode := 0;
  C.AddSchObject(A);
  Pl := SchServer.SchObjectFactory(ePolyline, eCreate_Default);
  Pl.InsertVertex := 1; Pl.SetState_Vertex(1, Pt(X0, Y0 + 400));
  Pl.InsertVertex := 2; Pl.SetState_Vertex(2, Pt(X0 + 400, Y0 + 400));
  Pl.OwnerPartId := 1; Pl.OwnerPartDisplayMode := 0;
  C.AddSchObject(Pl);
  Lb := SchServer.SchObjectFactory(eLabel, eCreate_Default);
  Lb.Location := Pt(X0 + 50, Y0 + 50); Lb.Text := 'LBL';
  Lb.OwnerPartId := 1; Lb.OwnerPartDisplayMode := 0;
  C.AddSchObject(Lb);
  P := SchServer.SchObjectFactory(ePin, eCreate_Default);
  P.Location := Pt(X0, Y0 + 100); P.Orientation := eRotate180; P.PinLength := MilsToCoord(100);
  P.Designator := '1'; P.Name := 'A';
  P.OwnerPartId := 1; P.OwnerPartDisplayMode := 0;
  C.AddSchObject(P);
end;

function NewComp(Name: String): Variant;
begin
  Result := SchServer.SchObjectFactory(eSchComponent, eCreate_Default);
  Result.LibReference := Name;
  Result.PartCount := 1;
  Result.CurrentPartID := 1;
  Result.DisplayMode := 0;
end;

procedure Fix(C: Variant; Tag: String);
begin
  try
    L(Tag + ' PinsMoveable before = ' + BoolToStr(C.PinsMoveable, True));
  except
    L(Tag + ' PinsMoveable read failed: ' + ExceptionMessage);
  end;
  try
    C.UpdatePrimitivesAccessibility;
    L(Tag + ' UpdatePrimitivesAccessibility ok');
  except
    L(Tag + ' UpdatePrimitivesAccessibility failed: ' + ExceptionMessage);
  end;
end;

procedure Body;
var D, Doc, Lib, LibD, C: Variant;
begin
  { ---------------- sheet }
  D := CreateNewDocumentFromDocumentKind('SCH');
  Doc := SchServer.GetCurrentSchDocument;
  D.SetFileName('%OUT%\p67_access.SchDoc');
  C := NewComp('WITHOUT');
  C.Location := Pt(1000, 1000);
  Doc.RegisterSchObjectInContainer(C);
  C.Designator.Text := 'U1';
  AddPrims(C, 1000, 1000);
  C.GraphicallyInvalidate;
  C := NewComp('WITH');
  C.Location := Pt(2000, 1000);
  Doc.RegisterSchObjectInContainer(C);
  C.Designator.Text := 'U2';
  AddPrims(C, 2000, 1000);
  Fix(C, 'sheet');
  C.GraphicallyInvalidate;
  D.DoFileSave('');
  L('sheet saved');
  Client.CloseDocument(D);

  { ---------------- library }
  LibD := Client.OpenNewDocument('SCHLIB', '%OUT%\p67_access.SchLib', '', False);
  Client.ShowDocument(LibD);
  Lib := SchServer.GetCurrentSchDocument;
  C := NewComp('WITHOUT');
  AddPrims(C, 0, 0);
  Lib.AddSchComponent(C);
  C := NewComp('WITH');
  AddPrims(C, 0, 0);
  Fix(C, 'lib');
  Lib.AddSchComponent(C);
  LibD.DoFileSave('SchLib');
  L('library saved');
  Client.CloseDocument(LibD);
end;
